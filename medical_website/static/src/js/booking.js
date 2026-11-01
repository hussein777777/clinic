import { Interaction } from "@web/public/interaction";
import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";

/**
 * Online booking form: shows the doctors of the chosen clinic, loads the free times,
 * and keeps the summary and the submit button in step with the choices.
 */
export class MedicalBooking extends Interaction {
    static selector = ".o_mw_booking";

    setup() {
        this.days = [];
        this.dayIndex = 0;
        this.selectedStart = this.el.dataset.start || "";
        this.requestId = 0;
    }

    start() {
        for (const input of this.el.querySelectorAll("input[name=department_id]")) {
            this.addListener(input, "change", () => this.onDepartment(true));
        }
        for (const input of this.el.querySelectorAll("input[name=doctor_id]")) {
            this.addListener(input, "change", () => this.loadSlots());
        }
        this.addListener(this.el, "submit", (ev) => {
            if (!this.el.querySelector("input[name=start]").value) {
                ev.preventDefault();
            }
        });
        this.onDepartment(false);
    }

    get departmentInput() {
        return this.el.querySelector("input[name=department_id]:checked");
    }

    get doctorInput() {
        return this.el.querySelector("input[name=doctor_id]:checked");
    }

    onDepartment(reset) {
        const department = this.departmentInput?.value;
        for (const label of this.el.querySelectorAll(".o_mw_choice_doctor")) {
            const ids = (label.dataset.departments || "").split(",");
            const visible = department && ids.includes(department);
            label.classList.toggle("d-none", !visible);
            if (!visible && label.querySelector("input").checked) {
                this.el.querySelector("input[name=doctor_id][data-any]").checked = true;
            }
        }
        this.el.querySelector(".o_mw_doctor_choices").classList.toggle("d-none", !department);
        if (reset) {
            this.selectedStart = "";
        }
        this.loadSlots();
    }

    async loadSlots() {
        const department = this.departmentInput?.value;
        const hint = this.el.querySelector(".o_mw_slots_hint");
        hint.classList.toggle("d-none", Boolean(department));
        this.days = [];
        this.render();
        if (!department) {
            return;
        }
        const requestId = ++this.requestId;
        this.loading = true;
        this.toggle(".o_mw_slots_loading", true);
        this.render();
        const result = await this.waitFor(
            rpc("/appointment/slots", {
                department_id: parseInt(department),
                doctor_id: parseInt(this.doctorInput?.value) || false,
            })
        );
        if (requestId !== this.requestId) {
            return; // a newer choice was made while this one loaded
        }
        this.loading = false;
        this.toggle(".o_mw_slots_loading", false);
        this.days = result.days || [];
        const kept = this.days.findIndex((day) => day.slots.some((slot) => slot.start === this.selectedStart));
        if (kept === -1) {
            this.selectedStart = "";
        }
        this.dayIndex = Math.max(kept, 0);
        this.render();
    }

    toggle(selector, show) {
        this.el.querySelector(selector).classList.toggle("d-none", !show);
    }

    button(className, label, sub, pressed, onClick) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = className + (pressed ? " is-selected" : "");
        btn.setAttribute("aria-pressed", pressed ? "true" : "false");
        const main = document.createElement("span");
        main.textContent = label;
        btn.appendChild(main);
        if (sub) {
            const small = document.createElement("span");
            small.className = "o_mw_day_sub";
            small.textContent = sub;
            btn.appendChild(small);
        }
        this.addListener(btn, "click", onClick);
        return btn;
    }

    render() {
        const daysEl = this.el.querySelector(".o_mw_days");
        const timesEl = this.el.querySelector(".o_mw_times");
        daysEl.replaceChildren();
        timesEl.replaceChildren();
        const department = this.departmentInput?.value;
        this.toggle(".o_mw_slots_empty", Boolean(department) && !this.days.length && !this.loading);
        this.days.forEach((day, index) => {
            daysEl.appendChild(
                this.button("o_mw_day", day.weekday, day.label, index === this.dayIndex, () => {
                    this.dayIndex = index;
                    this.render();
                })
            );
        });
        const day = this.days[this.dayIndex];
        for (const slot of day ? day.slots : []) {
            timesEl.appendChild(
                this.button("o_mw_time", slot.time, "", slot.start === this.selectedStart, () => {
                    this.selectedStart = slot.start;
                    this.render();
                })
            );
        }
        this.el.querySelector("input[name=start]").value = this.selectedStart;
        const chosenDay = this.days.find((d) => d.slots.some((s) => s.start === this.selectedStart));
        const chosenSlot = chosenDay?.slots.find((s) => s.start === this.selectedStart);
        this.el.querySelector(".o_mw_sum_department").textContent = this.departmentInput?.dataset.name || "-";
        this.el.querySelector(".o_mw_sum_doctor").textContent = this.doctorInput?.dataset.name || "-";
        this.el.querySelector(".o_mw_sum_day").textContent = chosenDay ? `${chosenDay.weekday} ${chosenDay.label}` : "-";
        this.el.querySelector(".o_mw_sum_time").textContent = chosenSlot ? chosenSlot.time : "-";
        this.el.querySelector(".o_mw_submit").disabled = !this.selectedStart;
    }
}

registry.category("public.interactions").add("medical_website.booking", MedicalBooking);
