import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("medical_website_booking_tour", {
    steps: () => [
        { trigger: ".o_mw_booking .o_mw_choice:has(input[name=department_id]) > span", run: "click" },
        { trigger: ".o_mw_days .o_mw_day" },
        { trigger: ".o_mw_times .o_mw_time", run: "click" },
        { trigger: ".o_mw_times .o_mw_time.is-selected" },
        { trigger: "input[name=name]", run: "edit Laila Hassan" },
        { trigger: "input[name=phone]", run: "edit +20 109 876 5432" },
        { trigger: "input[name=consent]", run: "click" },
        { trigger: ".o_mw_submit:enabled", run: "click", expectUnloadPage: true },
        { trigger: "h1:contains('Booking received')" },
    ],
});
