/** @odoo-module **/
import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("medical_ai_smart_dashboard_tour", {
    steps: () => [
        { trigger: ".o_sd_root .o_dd_kpis .o_dd_tile" },
        { trigger: ".o_sd_generate", run: "click" },
        { trigger: ".o_sd_insight_list .o_sd_insight" },
        { trigger: ".o_sd_input textarea", run: "edit What is the MRI pre-approval rule?" },
        { trigger: ".o_sd_input button", run: "click" },
        { trigger: ".o_sd_msg_assistant .o_sd_source" },
    ],
});
