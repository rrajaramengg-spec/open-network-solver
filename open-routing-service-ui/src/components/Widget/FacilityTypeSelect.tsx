import { useMemo, useState } from 'react';
import {
  Combobox,
  ComboboxButton,
  ComboboxInput,
  ComboboxOption,
  ComboboxOptions,
} from '@headlessui/react';
import type { CostMode, FacilityCategory, FacilityType } from '@/types/routing';
import { humanizeCategory } from '@/utils/categoryIcon';

export interface FacilityTypeSelectProps {
  value: FacilityType;
  onChange: (value: FacilityType) => void;
  /** Precomputed POI categories from `GET /v1/facility-categories`. */
  categories: FacilityCategory[];
}

interface TypeOption {
  value: FacilityType;
  label: string;
  /** Facility count, or null for the `all` sentinel. */
  count: number | null;
}

const ALL_OPTION: TypeOption = { value: 'all', label: 'All facilities', count: null };

/** Cap the rendered list so a 1000+ category catalog stays snappy. */
const MAX_RENDERED = 100;

/**
 * Data-driven facility-type picker. Because the catalog can hold 1000+ ingested
 * POI categories, this is a **searchable** combobox (Headless UI, WAI-ARIA
 * accessible) rather than a long native `<select>`: type to filter, then pick.
 * Lists the `All facilities` sentinel plus every ingested category with its
 * count — never a hardcoded subset.
 */
export function FacilityTypeSelect({
  value,
  onChange,
  categories,
}: FacilityTypeSelectProps): JSX.Element {
  const [query, setQuery] = useState('');

  const options = useMemo<TypeOption[]>(
    () => [
      ALL_OPTION,
      ...categories.map((c) => ({
        value: c.category,
        label: humanizeCategory(c.category),
        count: c.count,
      })),
    ],
    [categories],
  );

  const labelFor = (v: FacilityType): string =>
    options.find((o) => o.value === v)?.label ?? humanizeCategory(v);

  const q = query.trim().toLowerCase();
  const filtered =
    q === ''
      ? options
      : options.filter(
          (o) => o.label.toLowerCase().includes(q) || o.value.toLowerCase().includes(q),
        );
  const shown = filtered.slice(0, MAX_RENDERED);
  const hiddenCount = filtered.length - shown.length;

  return (
    <div>
      <label id="facility-type-label" className="block text-sm font-medium text-slate-200">
        Facility type
      </label>
      <Combobox
        value={value}
        onChange={(v: FacilityType | null) => {
          if (v != null) onChange(v);
        }}
        immediate
      >
        <div className="relative mt-1">
          <ComboboxInput
            aria-label="Facility type"
            aria-labelledby="facility-type-label"
            className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 pr-9 text-sm text-slate-100 placeholder:text-slate-500 focus:border-sky-500 focus:outline-none"
            displayValue={(v: FacilityType) => labelFor(v)}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search facility type…"
          />
          <ComboboxButton
            aria-label="Toggle facility type list"
            className="absolute inset-y-0 right-0 flex items-center px-2 text-slate-400 hover:text-slate-200"
          >
            <svg width="16" height="16" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
              <path
                fillRule="evenodd"
                d="M5.23 7.21a.75.75 0 0 1 1.06.02L10 11.17l3.71-3.94a.75.75 0 1 1 1.08 1.04l-4.25 4.5a.75.75 0 0 1-1.08 0l-4.25-4.5a.75.75 0 0 1 .02-1.06Z"
                clipRule="evenodd"
              />
            </svg>
          </ComboboxButton>
          <ComboboxOptions className="absolute z-20 mt-1 max-h-60 w-full overflow-auto rounded border border-slate-700 bg-slate-900 py-1 text-sm shadow-xl">
            {shown.length === 0 ? (
              <div className="px-3 py-2 text-slate-400">No matching type</div>
            ) : (
              shown.map((o) => (
                <ComboboxOption
                  key={o.value}
                  value={o.value}
                  className="cursor-pointer px-3 py-2 text-slate-200 data-[focus]:bg-slate-700 data-[selected]:font-medium data-[selected]:text-sky-300"
                >
                  {o.count != null ? `${o.label} (${o.count})` : o.label}
                </ComboboxOption>
              ))
            )}
            {hiddenCount > 0 ? (
              <div className="px-3 py-2 text-xs text-slate-500">
                +{hiddenCount} more — refine your search
              </div>
            ) : null}
          </ComboboxOptions>
        </div>
      </Combobox>
    </div>
  );
}

export interface CostModeToggleProps {
  value: CostMode;
  onChange: (value: CostMode) => void;
}

export function CostModeToggle({ value, onChange }: CostModeToggleProps): JSX.Element {
  return (
    <div>
      <span className="block text-sm font-medium text-slate-200">Cost mode</span>
      <div role="group" aria-label="Cost mode" className="mt-1 flex gap-1 text-sm">
        {(['distance', 'time'] as const).map((m) => (
          <button
            key={m}
            type="button"
            aria-pressed={value === m}
            className={
              'flex-1 rounded px-3 py-2 ' +
              (value === m
                ? 'bg-sky-600 text-white'
                : 'bg-slate-800 text-slate-300 hover:bg-slate-700')
            }
            onClick={() => onChange(m)}
          >
            {m === 'distance' ? 'Distance' : 'Time'}
          </button>
        ))}
      </div>
    </div>
  );
}
