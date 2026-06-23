-- osm2pgsql flex style for the closest-facility routing ETL.
--
-- Replaces osm2pgrouting (which OOMs above city scale). osm2pgsql streams via
-- libosmium with an on-disk flat-nodes store, so this scales to country-level
-- extracts. See openspec/changes/scalable-routing-etl (design D1-D4, D9, D10).
--
-- This style writes THREE staging tables into the `routing_next` schema:
--
--   routing_next.ways_import   raw highway ways as LineString(4326) + routing
--                              attributes (tag_id, maxspeed_forward, oneway).
--   routing_next.way_nodes     (way_id, node_id, seq) node membership, used by
--                              01_node_network.sql to split ways at shared
--                              OSM nodes (Option A — design D7).
--   routing_next.facilities    POIs from amenity/shop/tourism NODES and the
--                              centroids of amenity/shop/tourism WAYS/AREAS
--                              (design D3 + D10). vertex_id is filled later by
--                              03_facilities.sql; created NULL here.
--
-- The downstream contract (02_cost_columns.sql, 03_facilities.sql, the
-- closest_facility() function) reads `tag_id` (101-114, identical to
-- mapconfig.xml) and `maxspeed_forward`, so they run unchanged.

local SCHEMA = 'routing_next'

-- Highway class -> tag_id, identical to infra/etl/mapconfig.xml and
-- 02_cost_columns.sql / speed_tags.py. Only vehicle-routable classes are kept.
local TAG_ID_BY_HIGHWAY = {
    motorway       = 101,
    motorway_link  = 102,
    trunk          = 103,
    trunk_link     = 104,
    primary        = 105,
    primary_link   = 106,
    secondary      = 107,
    secondary_link = 108,
    tertiary       = 109,
    tertiary_link  = 110,
    residential    = 111,
    living_street  = 112,
    unclassified   = 113,
    service        = 114,
}

-- Facility POI keys (matches the previous node-only extractor).
local FACILITY_KEYS = { 'amenity', 'shop', 'tourism' }

-- ------------------------------------------------------------------ tables --

local ways_import = osm2pgsql.define_way_table('ways_import', {
    { column = 'osm_id',           type = 'int8', not_null = true },
    { column = 'tags',             type = 'jsonb' },
    { column = 'highway',          type = 'text' },
    { column = 'tag_id',           type = 'int',  not_null = true },
    { column = 'maxspeed_forward', type = 'int' },
    -- one-way direction: 1 = forward only, 0 = bidirectional, -1 = reverse only.
    { column = 'oneway',           type = 'int2', not_null = true },
    { column = 'geom',             type = 'linestring', projection = 4326, not_null = true },
}, { schema = SCHEMA })

-- way_nodes carries the ordered OSM node ids of each highway way so the SQL
-- noding step can split at shared nodes. No geometry column — the geometry
-- lives on ways_import; seq aligns 1:1 with ways_import.geom vertices.
local way_nodes = osm2pgsql.define_table({
    name = 'way_nodes',
    schema = SCHEMA,
    ids = { type = 'way', id_column = 'way_id' },
    columns = {
        { column = 'node_id', type = 'int8', not_null = true },
        { column = 'seq',     type = 'int',  not_null = true },
    },
    indexes = {},  -- index created in 01_node_network.sql after bulk load
})

local facilities = osm2pgsql.define_table({
    name = 'facilities',
    schema = SCHEMA,
    -- 'any' id so a single table holds both node and way (area) facilities.
    ids = { type = 'any', id_column = 'osm_id', type_column = 'osm_type' },
    columns = {
        -- Surrogate PK returned to the API as facility_id; also disambiguates a
        -- node and a way that happen to share an osm_id. PostgreSQL fills the
        -- serial default; osm2pgsql only creates the column (create_only).
        { column = 'id',        sql_type = 'serial', create_only = true },
        { column = 'name',      type = 'text' },
        { column = 'tags',      type = 'jsonb' },
        { column = 'geom',      type = 'point', projection = 4326, not_null = true },
        -- Filled by 03_facilities.sql (snap to nearest vertex); NULL on import.
        { column = 'vertex_id', sql_type = 'bigint', create_only = true },
    },
})

-- ----------------------------------------------------------------- helpers --

--- Parse an OSM maxspeed tag into an integer km/h, or nil.
-- Handles bare numbers ("50"), "50 km/h", and mph ("30 mph").
local function parse_maxspeed(value)
    if value == nil then
        return nil
    end
    local num = value:match('^%s*(%d+%.?%d*)')
    if num == nil then
        return nil  -- "walk", "none", "signals", etc. -> use class default downstream
    end
    local speed = tonumber(num)
    if speed == nil or speed <= 0 then
        return nil
    end
    if value:lower():find('mph') then
        speed = speed * 1.609344
    end
    return math.floor(speed + 0.5)
end

--- Map the OSM oneway tag (+ implicit one-way classes) to a direction int.
-- Returns 1 (forward), 0 (bidirectional), or -1 (reverse).
local function oneway_direction(tags, highway)
    local ow = tags.oneway
    if ow == 'yes' or ow == 'true' or ow == '1' then
        return 1
    elseif ow == '-1' or ow == 'reverse' then
        return -1
    elseif ow == 'no' or ow == 'false' or ow == '0' then
        return 0
    end
    -- Implicit one-way classes per OSM conventions.
    if highway == 'motorway' or highway == 'motorway_link' then
        return 1
    end
    if tags.junction == 'roundabout' or tags.junction == 'circular' then
        return 1
    end
    return 0
end

--- True if any FACILITY_KEYS tag is present.
local function is_facility(tags)
    for _, k in ipairs(FACILITY_KEYS) do
        if tags[k] ~= nil then
            return true
        end
    end
    return false
end

-- --------------------------------------------------------------- callbacks --

function osm2pgsql.process_node(object)
    if is_facility(object.tags) then
        facilities:insert({
            name = object.tags.name,
            tags = object.tags,
            geom = object:as_point(),
        })
    end
end

function osm2pgsql.process_way(object)
    local highway = object.tags.highway
    local tag_id = highway and TAG_ID_BY_HIGHWAY[highway] or nil

    if tag_id ~= nil then
        -- Routable highway way.
        local geom = object:as_linestring()
        if not geom:is_null() then
            ways_import:insert({
                osm_id           = object.id,
                tags             = object.tags,
                highway          = highway,
                tag_id           = tag_id,
                maxspeed_forward = parse_maxspeed(object.tags.maxspeed),
                oneway           = oneway_direction(object.tags, highway),
                geom             = geom,
            })
            -- Record ordered node membership for the noding step.
            for i, node_id in ipairs(object.nodes) do
                way_nodes:insert({ way_id = object.id, node_id = node_id, seq = i })
            end
        end
    elseif is_facility(object.tags) and object.is_closed then
        -- Area-mapped facility (e.g. hospital building polygon) -> centroid.
        local centroid = object:as_polygon():centroid()
        if not centroid:is_null() then
            facilities:insert({
                name = object.tags.name,
                tags = object.tags,
                geom = centroid,
            })
        end
    end
end
