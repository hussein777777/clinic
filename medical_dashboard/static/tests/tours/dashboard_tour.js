/** @odoo-module **/
import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("medical_dashboard_tour", {
    steps: () => [
        { trigger: ".o_dd_root .o_dd_kpis .o_dd_tile" },
        { trigger: ".o_dd_periods button:contains('Last 90 days')", run: "click" },
        { trigger: ".o_dd_periods button.btn-primary:contains('Last 90 days')" },
        { trigger: ".o_dd_leaks .o_dd_leak" },
    ],
});
