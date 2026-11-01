/** @odoo-module **/
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component } from "@odoo/owl";

const UPPER = [18, 17, 16, 15, 14, 13, 12, 11, 21, 22, 23, 24, 25, 26, 27, 28];
const LOWER = [48, 47, 46, 45, 44, 43, 42, 41, 31, 32, 33, 34, 35, 36, 37, 38];
const UPPER_PRIMARY = [55, 54, 53, 52, 51, 61, 62, 63, 64, 65];
const LOWER_PRIMARY = [85, 84, 83, 82, 81, 71, 72, 73, 74, 75];

const LEGEND = [
    ["sound", _t("Sound")],
    ["caries", _t("Caries")],
    ["filled", _t("Filled")],
    ["rct", _t("Root canal")],
    ["crown", _t("Crown / Bridge")],
    ["implant", _t("Implant")],
    ["missing", _t("Missing / Extracted")],
];

export class DentalToothChart extends Component {
    static template = "dental_core.DentalToothChart";
    static props = { ...standardFieldProps };

    get data() {
        return this.props.record.data[this.props.name] || {};
    }
    get legend() {
        return LEGEND;
    }
    get hasPrimary() {
        return Object.keys(this.data).some((code) => Number(code) >= 51);
    }
    rows() {
        const rows = [
            { key: "upper", teeth: UPPER },
            { key: "lower", teeth: LOWER },
        ];
        if (this.hasPrimary) {
            rows.splice(1, 0, { key: "upper_primary", teeth: UPPER_PRIMARY },
                { key: "lower_primary", teeth: LOWER_PRIMARY });
        }
        return rows;
    }
    toothInfo(code) {
        return this.data[String(code)] || null;
    }
    toothClass(code) {
        const info = this.toothInfo(code);
        let cond = info ? info.condition : "sound";
        if (cond === "bridge") {
            cond = "crown";
        }
        if (cond === "extracted") {
            cond = "missing";
        }
        return `o_dental_tooth o_dental_tooth_${cond}`;
    }
    toothTitle(code) {
        const info = this.toothInfo(code);
        if (!info) {
            return `${code}`;
        }
        const surfaces = info.surfaces ? ` (${info.surfaces})` : "";
        return `${code}: ${info.label}${surfaces} - ${info.date}`;
    }
}

export const dentalToothChart = {
    component: DentalToothChart,
    displayName: _t("Dental Tooth Chart"),
    supportedTypes: ["json"],
};

registry.category("fields").add("dental_tooth_chart", dentalToothChart);
