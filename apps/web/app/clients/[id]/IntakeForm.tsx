"use client";

import { useEffect, useState } from "react";

type Address = {
  street?: string;
  city?: string;
  state?: string;
  zip?: string;
};

export type IntakeFields = {
  legal_name: string | null;
  entity_type: string | null;
  formation_state: string | null;
  ein: string | null;
  principal_address: Address | null;
  officer_name: string | null;
  officer_title: string | null;
  officer_email: string | null;
  primary_contact_name: string | null;
  primary_contact_email: string | null;
  primary_contact_phone: string | null;
  target_states: string[] | null;
  intends_international: boolean;
  estimated_monthly_revenue: number | null;
  ocn: string | null;
};

type FormState = {
  legal_name: string;
  entity_type: string;
  formation_state: string;
  ein: string;
  address_street: string;
  address_city: string;
  address_state: string;
  address_zip: string;
  officer_name: string;
  officer_title: string;
  officer_email: string;
  primary_contact_name: string;
  primary_contact_email: string;
  primary_contact_phone: string;
  target_states: string;
  intends_international: boolean;
  estimated_monthly_revenue: string;
  ocn: string;
};

const EMPTY_FORM: FormState = {
  legal_name: "",
  entity_type: "",
  formation_state: "",
  ein: "",
  address_street: "",
  address_city: "",
  address_state: "",
  address_zip: "",
  officer_name: "",
  officer_title: "",
  officer_email: "",
  primary_contact_name: "",
  primary_contact_email: "",
  primary_contact_phone: "",
  target_states: "",
  intends_international: false,
  estimated_monthly_revenue: "",
  ocn: "",
};

function fromFields(i: IntakeFields | null): FormState {
  if (!i) return EMPTY_FORM;
  const a = i.principal_address ?? {};
  return {
    legal_name: i.legal_name ?? "",
    entity_type: i.entity_type ?? "",
    formation_state: i.formation_state ?? "",
    ein: i.ein ?? "",
    address_street: a.street ?? "",
    address_city: a.city ?? "",
    address_state: a.state ?? "",
    address_zip: a.zip ?? "",
    officer_name: i.officer_name ?? "",
    officer_title: i.officer_title ?? "",
    officer_email: i.officer_email ?? "",
    primary_contact_name: i.primary_contact_name ?? "",
    primary_contact_email: i.primary_contact_email ?? "",
    primary_contact_phone: i.primary_contact_phone ?? "",
    target_states: (i.target_states ?? []).join(", "),
    intends_international: !!i.intends_international,
    estimated_monthly_revenue:
      i.estimated_monthly_revenue == null
        ? ""
        : String(i.estimated_monthly_revenue),
    ocn: i.ocn ?? "",
  };
}

function toPayload(f: FormState): Partial<IntakeFields> {
  const states = f.target_states
    .split(",")
    .map((s) => s.trim().toUpperCase())
    .filter(Boolean);
  const address: Address = {
    street: f.address_street.trim(),
    city: f.address_city.trim(),
    state: f.address_state.trim().toUpperCase(),
    zip: f.address_zip.trim(),
  };
  const addressEmpty =
    !address.street && !address.city && !address.state && !address.zip;
  const revenue =
    f.estimated_monthly_revenue.trim() === ""
      ? null
      : Number(f.estimated_monthly_revenue);
  return {
    legal_name: f.legal_name.trim() || null,
    entity_type: f.entity_type.trim() || null,
    formation_state: f.formation_state.trim().toUpperCase() || null,
    ein: f.ein.trim() || null,
    principal_address: addressEmpty ? null : address,
    officer_name: f.officer_name.trim() || null,
    officer_title: f.officer_title.trim() || null,
    officer_email: f.officer_email.trim() || null,
    primary_contact_name: f.primary_contact_name.trim() || null,
    primary_contact_email: f.primary_contact_email.trim() || null,
    primary_contact_phone: f.primary_contact_phone.trim() || null,
    target_states: states.length ? states : null,
    intends_international: f.intends_international,
    estimated_monthly_revenue:
      revenue === null || Number.isNaN(revenue) ? null : revenue,
    ocn: f.ocn.trim() || null,
  };
}

function FieldRow({
  label,
  required,
  children,
  hint,
}: {
  label: string;
  required?: boolean;
  children: React.ReactNode;
  hint?: string;
}) {
  return (
    <label className="block">
      <span className="text-xs text-slate-600">
        {label}
        {required && <span className="text-red-600"> *</span>}
      </span>
      <div className="mt-1">{children}</div>
      {hint && <p className="mt-1 text-xs text-slate-400">{hint}</p>}
    </label>
  );
}

const INPUT_CLS =
  "w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm focus:border-slate-500 focus:outline-none";

export default function IntakeForm({
  intake,
  busy,
  onSave,
}: {
  intake: IntakeFields | null;
  busy: boolean;
  onSave: (payload: Partial<IntakeFields>) => Promise<void> | void;
}) {
  const [form, setForm] = useState<FormState>(() => fromFields(intake));
  // When the saved intake changes (e.g. after a server-side save / page
  // refresh), refresh the form to match — but don't clobber in-flight
  // edits while the operator is typing (we re-sync only when busy=false).
  useEffect(() => {
    setForm(fromFields(intake));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intake]);

  function field<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  return (
    <form
      className="mt-3 space-y-6"
      onSubmit={(e) => {
        e.preventDefault();
        onSave(toPayload(form));
      }}
    >
      <fieldset>
        <legend className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          Entity basics
        </legend>
        <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <FieldRow label="Legal name" required>
            <input
              className={INPUT_CLS}
              value={form.legal_name}
              onChange={(e) => field("legal_name", e.target.value)}
            />
          </FieldRow>
          <FieldRow label="Entity type" required hint="e.g. LLC, Corp, Inc.">
            <input
              className={INPUT_CLS}
              value={form.entity_type}
              onChange={(e) => field("entity_type", e.target.value)}
            />
          </FieldRow>
          <FieldRow label="Formation state" required hint="2-letter, e.g. DE">
            <input
              className={INPUT_CLS}
              maxLength={2}
              value={form.formation_state}
              onChange={(e) =>
                field("formation_state", e.target.value.toUpperCase())
              }
            />
          </FieldRow>
          <FieldRow label="EIN" required>
            <input
              className={INPUT_CLS}
              placeholder="XX-XXXXXXX"
              value={form.ein}
              onChange={(e) => field("ein", e.target.value)}
            />
          </FieldRow>
          <FieldRow
            label="OCN"
            hint="Operating Company Number (carrier portals)"
          >
            <input
              className={INPUT_CLS}
              value={form.ocn}
              onChange={(e) => field("ocn", e.target.value)}
            />
          </FieldRow>
        </div>
      </fieldset>

      <fieldset>
        <legend className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          Principal address
        </legend>
        <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div className="sm:col-span-3">
            <FieldRow label="Street" required>
              <input
                className={INPUT_CLS}
                value={form.address_street}
                onChange={(e) => field("address_street", e.target.value)}
              />
            </FieldRow>
          </div>
          <FieldRow label="City" required>
            <input
              className={INPUT_CLS}
              value={form.address_city}
              onChange={(e) => field("address_city", e.target.value)}
            />
          </FieldRow>
          <FieldRow label="State" required>
            <input
              className={INPUT_CLS}
              maxLength={2}
              value={form.address_state}
              onChange={(e) =>
                field("address_state", e.target.value.toUpperCase())
              }
            />
          </FieldRow>
          <FieldRow label="ZIP" required>
            <input
              className={INPUT_CLS}
              value={form.address_zip}
              onChange={(e) => field("address_zip", e.target.value)}
            />
          </FieldRow>
        </div>
      </fieldset>

      <fieldset>
        <legend className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          Signing officer
        </legend>
        <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <FieldRow label="Name" required>
            <input
              className={INPUT_CLS}
              value={form.officer_name}
              onChange={(e) => field("officer_name", e.target.value)}
            />
          </FieldRow>
          <FieldRow label="Title" required>
            <input
              className={INPUT_CLS}
              value={form.officer_title}
              onChange={(e) => field("officer_title", e.target.value)}
            />
          </FieldRow>
          <FieldRow label="Email" required>
            <input
              className={INPUT_CLS}
              type="email"
              value={form.officer_email}
              onChange={(e) => field("officer_email", e.target.value)}
            />
          </FieldRow>
        </div>
      </fieldset>

      <fieldset>
        <legend className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          Primary contact (day-to-day)
        </legend>
        <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <FieldRow label="Name" required>
            <input
              className={INPUT_CLS}
              value={form.primary_contact_name}
              onChange={(e) => field("primary_contact_name", e.target.value)}
            />
          </FieldRow>
          <FieldRow label="Email" required>
            <input
              className={INPUT_CLS}
              type="email"
              value={form.primary_contact_email}
              onChange={(e) => field("primary_contact_email", e.target.value)}
            />
          </FieldRow>
          <FieldRow label="Phone" required>
            <input
              className={INPUT_CLS}
              value={form.primary_contact_phone}
              onChange={(e) => field("primary_contact_phone", e.target.value)}
            />
          </FieldRow>
        </div>
      </fieldset>

      <fieldset>
        <legend className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          Filings &amp; scope
        </legend>
        <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <FieldRow
            label="Target states"
            required
            hint="Comma-separated 2-letter codes, e.g. TX, CA, NY. Drives state-CPCN requirements."
          >
            <input
              className={INPUT_CLS}
              value={form.target_states}
              onChange={(e) => field("target_states", e.target.value)}
            />
          </FieldRow>
          <FieldRow
            label="Estimated monthly revenue (USD)"
            required
            hint="Used for FCC 499 surcharge tiering."
          >
            <input
              className={INPUT_CLS}
              type="number"
              min={0}
              value={form.estimated_monthly_revenue}
              onChange={(e) =>
                field("estimated_monthly_revenue", e.target.value)
              }
            />
          </FieldRow>
          <label className="flex items-center gap-2 sm:col-span-2">
            <input
              type="checkbox"
              checked={form.intends_international}
              onChange={(e) =>
                field("intends_international", e.target.checked)
              }
            />
            <span className="text-sm text-slate-700">
              Intends to offer international service
            </span>
            <span className="text-xs text-slate-400">
              (pulls in FCC Section 214 support requirement)
            </span>
          </label>
        </div>
      </fieldset>

      <button
        type="submit"
        disabled={busy}
        className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
      >
        Save intake
      </button>
    </form>
  );
}
