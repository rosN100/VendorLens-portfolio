import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import App, { validateDraft } from "./App";

describe("scaffold", () => {
  it("shows the project and next component", () => {
    const html = renderToStaticMarkup(<App />);
    expect(html).toContain("VendorLens");
    expect(html).toContain("Case intake");
  });

  it("shows the case form and initial loading state", () => {
    const html = renderToStaticMarkup(<App />);
    expect(html).toContain("Create case");
    expect(html).toContain("Pilot actor");
    expect(html).toContain("Policy v1.0");
    expect(html).toContain("Loading cases");
  });

  it("validates required builder-facing fields", () => {
    expect(validateDraft({ actor: "pilot_analyst", requestId: "id", vendorName: "", serviceName: "Service", assessmentDate: "2026-09-30" })).toBe("Enter a vendor name.");
    expect(validateDraft({ actor: "pilot_analyst", requestId: "id", vendorName: "Vendor", serviceName: "Service", assessmentDate: "2026-09-30" })).toBeNull();
  });
});
