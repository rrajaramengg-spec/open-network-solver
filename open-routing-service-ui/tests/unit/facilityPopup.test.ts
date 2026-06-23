import { describe, it, expect } from 'vitest';
import { buildFacilityPopupHTML } from '@/components/Map/FacilityPopup';

describe('buildFacilityPopupHTML', () => {
  const base = {
    facilityId: 7,
    rank: 2,
    name: 'Engine 7',
    category: 'fire_station',
    totalDistanceM: 123.6,
    totalTimeS: 45.4,
    tags: {
      'addr:housenumber': '12',
      'addr:street': 'Main St',
      phone: '555-1234',
    },
  };

  it('renders the name, humanised category, rank, rounded distance and time', () => {
    const html = buildFacilityPopupHTML(base);
    expect(html).toContain('Engine 7');
    expect(html).toContain('Fire Station');
    expect(html).toContain('#2');
    expect(html).toContain('124 m');
    expect(html).toContain('45 s');
  });

  it('renders selected OSM tags including a composed address', () => {
    const html = buildFacilityPopupHTML(base);
    expect(html).toContain('12 Main St');
    expect(html).toContain('555-1234');
  });

  it('falls back to `Facility #<id>` when the name is absent', () => {
    const html = buildFacilityPopupHTML({ ...base, name: null });
    expect(html).toContain('Facility #7');
  });

  it('escapes HTML in untrusted values', () => {
    const html = buildFacilityPopupHTML({
      ...base,
      name: '<img src=x onerror=alert(1)>',
    });
    expect(html).not.toContain('<img src=x');
    expect(html).toContain('&lt;img');
  });

  it('omits tag rows when tags are absent', () => {
    const html = buildFacilityPopupHTML({ ...base, tags: null });
    expect(html).not.toContain('Phone');
  });
});
