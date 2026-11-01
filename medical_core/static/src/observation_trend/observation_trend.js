/** @odoo-module **/
import { Component, onMounted, onPatched, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { localization } from "@web/core/l10n/localization";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";

const FLAG_LABELS = {
    low: _t("Low"),
    high: _t("High"),
    critical_low: _t("Critically low"),
    critical_high: _t("Critically high"),
};
const CATEGORY_ORDER = ["vital", "lab", "exam", "score", "growth"];

function fmt(value, decimals) {
    if (value === null || value === undefined) {
        return "";
    }
    return Number(value).toLocaleString(localization.code?.replace("_", "-") || undefined, {
        minimumFractionDigits: Math.max(decimals, 0),
        maximumFractionDigits: Math.max(decimals, 0),
    });
}

/**
 * Patient measurements over time: one chip per measurement (latest value + flag),
 * and a line chart of the selected one with its normal range shaded.
 */
export class ObservationTrend extends Component {
    static template = "medical_core.ObservationTrend";
    static props = { ...standardWidgetProps };

    setup() {
        this.orm = useService("orm");
        this.canvasRef = useRef("canvas");
        this.state = useState({ series: [], selected: null, table: false, loading: true });
        this.chart = null;
        onWillStart(async () => {
            await loadBundle("web.chartjs_lib");
            await this.load();
        });
        onMounted(() => this.renderChart());
        onPatched(() => this.renderChart());
        onWillUnmount(() => this.destroyChart());
    }

    async load() {
        const resId = this.props.record.resId;
        if (!resId) {
            this.state.loading = false;
            return;
        }
        const series = await this.orm.call("medical.observation", "get_trend", [resId]);
        series.sort((a, b) => CATEGORY_ORDER.indexOf(a.category) - CATEGORY_ORDER.indexOf(b.category));
        this.state.series = series;
        if (!series.find((s) => s.type_id === this.state.selected)) {
            this.state.selected = series.length ? series[0].type_id : null;
        }
        this.state.loading = false;
    }

    get current() {
        return this.state.series.find((s) => s.type_id === this.state.selected);
    }

    get chips() {
        return this.state.series.map((s) => {
            const last = s.points[s.points.length - 1];
            const flag = last && last.flag !== "normal" ? last.flag : false;
            return {
                id: s.type_id,
                name: s.name,
                value: last ? `${fmt(last.value, s.decimals)} ${s.unit}`.trim() : "",
                flag,
                flagLabel: flag ? FLAG_LABELS[flag] : "",
                critical: flag && flag.startsWith("critical"),
                count: s.count,
            };
        });
    }

    get rows() {
        const s = this.current;
        if (!s) {
            return [];
        }
        return [...s.points].reverse().map((p) => ({
            label: p.label,
            value: fmt(p.value, s.decimals),
            flag: p.flag !== "normal" ? p.flag : false,
            flagLabel: p.flag !== "normal" ? FLAG_LABELS[p.flag] || "" : "",
            critical: p.flag && p.flag.startsWith("critical"),
        }));
    }

    get rangeText() {
        const s = this.current;
        if (!s || (s.normal_min === null && s.normal_max === null)) {
            return "";
        }
        if (s.normal_min !== null && s.normal_max !== null) {
            return _t("Shaded band: normal range %(min)s – %(max)s %(unit)s", {
                min: fmt(s.normal_min, s.decimals), max: fmt(s.normal_max, s.decimals), unit: s.unit,
            });
        }
        if (s.normal_min !== null) {
            return _t("Normal: %(min)s %(unit)s or more", { min: fmt(s.normal_min, s.decimals), unit: s.unit });
        }
        return _t("Normal: up to %(max)s %(unit)s", { max: fmt(s.normal_max, s.decimals), unit: s.unit });
    }

    get changeText() {
        const s = this.current;
        if (!s || s.points.length < 2) {
            return "";
        }
        const first = s.points[0];
        const last = s.points[s.points.length - 1];
        const diff = last.value - first.value;
        if (!diff) {
            return _t("No change since %s", first.label);
        }
        const sign = diff > 0 ? "+" : "−";
        return _t("%(change)s %(unit)s since %(date)s", {
            change: `${sign}${fmt(Math.abs(diff), s.decimals)}`, unit: s.unit, date: first.label,
        });
    }

    select(typeId) {
        this.state.selected = typeId;
    }

    toggleTable() {
        this.state.table = !this.state.table;
    }

    destroyChart() {
        if (this.chart) {
            this.chart.destroy();
            this.chart = null;
        }
    }

    renderChart() {
        this.destroyChart();
        const canvas = this.canvasRef.el;
        const s = this.current;
        if (!canvas || !s || this.state.table || !window.Chart) {
            return;
        }
        const css = getComputedStyle(canvas);
        const token = (name) => css.getPropertyValue(name).trim();
        const rtl = localization.direction === "rtl";
        const pointColor = (ctx) => {
            const flag = s.points[ctx.dataIndex]?.flag;
            if (flag && flag.startsWith("critical")) {
                return token("--mo-critical");
            }
            if (flag && flag !== "normal") {
                return token("--mo-serious");
            }
            return token("--mo-series");
        };
        const values = s.points.map((p) => p.value);
        const datasets = [];
        if (s.normal_min !== null || s.normal_max !== null) {
            const lo = s.normal_min ?? Math.min(...values, 0);
            const hi = s.normal_max ?? Math.max(...values) * 1.1;
            datasets.push({
                data: values.map(() => hi), fill: "+1", backgroundColor: token("--mo-band"), borderWidth: 0,
                pointRadius: 0, pointHitRadius: 0, label: "_band_hi",
            });
            datasets.push({
                data: values.map(() => lo), fill: false, borderWidth: 0, pointRadius: 0, pointHitRadius: 0,
                label: "_band_lo",
            });
        }
        datasets.push({
            label: s.name,
            data: values,
            borderColor: token("--mo-series"),
            borderWidth: 2,
            tension: 0,
            pointRadius: 4,
            pointHoverRadius: 6,
            pointBackgroundColor: pointColor,
            pointBorderColor: token("--mo-surface"),
            pointBorderWidth: 2,
            pointHitRadius: 12,
        });
        this.chart = new window.Chart(canvas, {
            type: "line",
            data: { labels: s.points.map((p) => p.label), datasets },
            options: {
                maintainAspectRatio: false,
                animation: { duration: 200 },
                interaction: { mode: "index", intersect: false },
                scales: {
                    x: {
                        reverse: rtl,
                        grid: { display: false },
                        border: { color: token("--mo-axis") },
                        ticks: { color: token("--mo-secondary"), maxTicksLimit: 8, autoSkip: true },
                    },
                    y: {
                        position: rtl ? "right" : "left",
                        grace: "10%",
                        grid: { color: token("--mo-grid"), drawTicks: false },
                        border: { display: false },
                        ticks: { color: token("--mo-muted"), padding: 6, maxTicksLimit: 5 },
                    },
                },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        rtl,
                        filter: (item) => !String(item.dataset.label).startsWith("_band"),
                        backgroundColor: token("--mo-tooltip-bg"),
                        titleColor: token("--mo-tooltip-ink"),
                        bodyColor: token("--mo-tooltip-ink"),
                        displayColors: false,
                        callbacks: {
                            label: (item) => {
                                const point = s.points[item.dataIndex];
                                const flag = point.flag && point.flag !== "normal" ? ` · ${FLAG_LABELS[point.flag]}` : "";
                                return `${fmt(point.value, s.decimals)} ${s.unit}${flag}`;
                            },
                        },
                    },
                },
            },
        });
    }
}

registry.category("view_widgets").add("medical_observation_trend", {
    component: ObservationTrend,
});
