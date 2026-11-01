/** @odoo-module **/
import { Component, onMounted, onPatched, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { localization } from "@web/core/l10n/localization";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";

const CURVES = [
    { key: "97", token: "--gc-outer", dash: [4, 4], width: 1 },
    { key: "85", token: "--gc-inner", dash: [], width: 1 },
    { key: "50", token: "--gc-median", dash: [], width: 1.5 },
    { key: "15", token: "--gc-inner", dash: [], width: 1 },
    { key: "3", token: "--gc-outer", dash: [4, 4], width: 1 },
];

function fmt(value, decimals = 1) {
    if (value === null || value === undefined) {
        return "";
    }
    return Number(value).toLocaleString(localization.code?.replace("_", "-") || undefined, {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
    });
}

/** WHO growth chart (0-5 years) for the child on the patient form. */
export class GrowthChart extends Component {
    static template = "medical_pediatrics.GrowthChart";
    static props = { ...standardWidgetProps };

    setup() {
        this.orm = useService("orm");
        this.canvasRef = useRef("canvas");
        this.state = useState({ data: null, selected: "wfa", table: false });
        onWillStart(async () => {
            await loadBundle("web.chartjs_lib");
            if (this.props.record.resId) {
                this.state.data = await this.orm.call("res.partner", "get_growth_data", [[this.props.record.resId]]);
            }
        });
        onMounted(() => this.renderChart());
        onPatched(() => this.renderChart());
        onWillUnmount(() => this.chart?.destroy());
    }

    get indicators() {
        return this.state.data?.indicators || [];
    }

    get current() {
        return this.indicators.find((i) => i.key === this.state.selected);
    }

    get latest() {
        const points = this.current?.points || [];
        return points[points.length - 1];
    }

    get rows() {
        const ind = this.current;
        return [...(ind?.points || [])].reverse().map((p) => ({
            ...p,
            ageText: this.ageText(p.age),
            valueText: fmt(p.value, ind.decimals),
            zText: p.z === null ? "" : `${p.z > 0 ? "+" : ""}${fmt(p.z, 1)}`,
            pText: p.percentile === null ? "" : fmt(p.percentile, 0),
            abnormal: p.z !== null && Math.abs(p.z) > 2,
        }));
    }

    ageText(months) {
        if (months < 24) {
            return _t("%s months", fmt(months, 1));
        }
        const years = Math.floor(months / 12);
        return _t("%(y)s y %(m)s m", { y: years, m: Math.round(months - years * 12) });
    }

    isAbnormal(point) {
        return point && point.z !== null && Math.abs(point.z) > 2;
    }

    select(key) {
        this.state.selected = key;
    }

    toggleTable() {
        this.state.table = !this.state.table;
    }

    renderChart() {
        this.chart?.destroy();
        this.chart = null;
        const canvas = this.canvasRef.el;
        const ind = this.current;
        if (!canvas || !ind || this.state.table || !window.Chart) {
            return;
        }
        const css = getComputedStyle(canvas);
        const token = (name) => css.getPropertyValue(name).trim();
        const rtl = localization.direction === "rtl";
        const datasets = CURVES.map((c) => ({
            label: _t("%sth percentile", c.key),
            data: ind.curves[c.key].map((p) => ({ x: p.age, y: p.value })),
            borderColor: token(c.token),
            borderDash: c.dash,
            borderWidth: c.width,
            pointRadius: 0,
            pointHitRadius: 0,
            tension: 0.3,
        }));
        datasets.push({
            label: _t("Child"),
            data: ind.points.map((p) => ({ x: p.age, y: p.value })),
            borderColor: token("--gc-series"),
            backgroundColor: ind.points.map((p) => (this.isAbnormal(p) ? token("--gc-serious") : token("--gc-series"))),
            borderWidth: 2,
            pointRadius: 4,
            pointHoverRadius: 6,
            pointBorderColor: token("--gc-surface"),
            pointBorderWidth: 2,
            pointHitRadius: 12,
            tension: 0,
        });
        this.chart = new window.Chart(canvas, {
            type: "line",
            data: { datasets },
            options: {
                maintainAspectRatio: false,
                animation: { duration: 200 },
                parsing: false,
                scales: {
                    x: {
                        type: "linear",
                        min: 0,
                        max: 60,
                        reverse: rtl,
                        grid: { color: token("--gc-grid"), drawTicks: false },
                        border: { color: token("--gc-axis") },
                        ticks: { color: token("--gc-secondary"), stepSize: 6, padding: 6 },
                        title: { display: true, text: _t("Age (months)"), color: token("--gc-secondary") },
                    },
                    y: {
                        position: rtl ? "right" : "left",
                        grid: { color: token("--gc-grid"), drawTicks: false },
                        border: { display: false },
                        ticks: { color: token("--gc-muted"), padding: 6 },
                        title: { display: true, text: ind.unit, color: token("--gc-secondary") },
                    },
                },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        rtl,
                        filter: (item) => item.datasetIndex === CURVES.length,
                        backgroundColor: token("--gc-tooltip-bg"),
                        titleColor: token("--gc-tooltip-ink"),
                        bodyColor: token("--gc-tooltip-ink"),
                        displayColors: false,
                        callbacks: {
                            title: (items) => (items.length ? this.ageText(ind.points[items[0].dataIndex].age) : ""),
                            label: (item) => {
                                const p = ind.points[item.dataIndex];
                                return [`${fmt(p.value, ind.decimals)} ${ind.unit}`,
                                    `z ${p.z > 0 ? "+" : ""}${fmt(p.z, 1)} · P${fmt(p.percentile, 0)}`, p.status];
                            },
                        },
                    },
                },
            },
        });
    }
}

registry.category("view_widgets").add("medical_growth_chart", { component: GrowthChart });
