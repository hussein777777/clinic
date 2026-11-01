/** @odoo-module **/
import { Component, onMounted, onPatched, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { localization } from "@web/core/l10n/localization";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

// --------------------------------------------------------------------- formatting
function groupDigits(intPart) {
    const sep = localization.thousandsSep ?? ",";
    return intPart.replace(/\B(?=(\d{3})+(?!\d))/g, sep);
}

export function formatNumber(value, digits = 0) {
    const fixed = Math.abs(value || 0).toFixed(digits);
    const [intPart, dec] = fixed.split(".");
    const sign = value < 0 ? "-" : "";
    return sign + groupDigits(intPart) + (dec ? (localization.decimalPoint || ".") + dec : "");
}

export function formatCompact(value) {
    const abs = Math.abs(value || 0);
    const sign = value < 0 ? "-" : "";
    if (abs >= 1e6) {
        return sign + formatNumber(abs / 1e6, abs >= 1e7 ? 0 : 1) + "M";
    }
    if (abs >= 1e3) {
        return sign + formatNumber(abs / 1e3, abs >= 1e4 ? 0 : 1) + "K";
    }
    return sign + formatNumber(abs, 0);
}

function withCurrency(text, currency) {
    if (!currency) {
        return text;
    }
    return currency.position === "before" ? `${currency.symbol} ${text}` : `${text} ${currency.symbol}`;
}

const WEEKDAYS = {
    0: _t("Mon"),
    1: _t("Tue"),
    2: _t("Wed"),
    3: _t("Thu"),
    4: _t("Fri"),
    5: _t("Sat"),
    6: _t("Sun"),
};

// ---------------------------------------------------------------------- chart card
export class MedicalChartCard extends Component {
    static template = "medical_dashboard.ChartCard";
    static props = {
        title: String,
        subtitle: { type: String, optional: true },
        kind: String, // "stacked" | "hbar"
        labels: Array,
        series: Array, // [{label, values, colorVar}]
        unit: String, // "money" | "percent" | "number"
        currency: { type: Object, optional: true },
        height: { type: Number, optional: true },
        slots: { type: Object, optional: true },
    };

    setup() {
        this.canvasRef = useRef("canvas");
        this.state = useState({ table: false });
        this.chart = null;
        this.renderedKey = null;
        onMounted(() => this.renderChart());
        // a parent re-render hands new arrays with the same values: only redraw when the data changed
        onPatched(() => {
            if (this.dataKey !== this.renderedKey || !this.chart) {
                this.renderChart();
            }
        });
        onWillUnmount(() => this.destroyChart());
    }

    get dataKey() {
        return JSON.stringify([this.props.kind, this.props.labels, this.props.series, this.props.unit, this.state.table]);
    }

    get isEmpty() {
        return !this.props.series.some((s) => s.values.some((v) => v));
    }

    get chartHeight() {
        if (this.props.height) {
            return this.props.height;
        }
        return this.props.kind === "hbar" ? Math.max(120, this.props.labels.length * 36 + 24) : 280;
    }

    get tableRows() {
        return this.props.labels.map((label, index) => ({
            label,
            values: this.props.series.map((s) => this.formatValue(s.values[index])),
            total: this.props.series.length > 1
                ? this.formatValue(this.props.series.reduce((sum, s) => sum + (s.values[index] || 0), 0))
                : null,
        }));
    }

    formatValue(value) {
        if (this.props.unit === "percent") {
            return `${formatNumber(value, 1)}%`;
        }
        if (this.props.unit === "number") {
            return formatNumber(value, 0);
        }
        return withCurrency(formatNumber(value, 0), this.props.currency);
    }

    formatAxis(value) {
        if (this.props.unit === "percent") {
            return `${formatNumber(value, 0)}%`;
        }
        return formatCompact(value);
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
        const firstDraw = this.renderedKey === null;
        this.renderedKey = this.dataKey;
        this.destroyChart();
        const canvas = this.canvasRef.el;
        if (!canvas || this.state.table || this.isEmpty || !window.Chart) {
            return;
        }
        const css = getComputedStyle(canvas);
        const token = (name) => css.getPropertyValue(name).trim();
        const rtl = localization.direction === "rtl";
        const horizontal = this.props.kind === "hbar";
        const stacked = this.props.kind === "stacked";
        const surface = token("--dd-surface");
        const series = this.props.series;
        // the topmost non-zero segment of each column gets the rounded end; the others get the 2px surface gap
        const isTop = (datasetIndex, dataIndex) =>
            !series.slice(datasetIndex + 1).some((s) => s.values[dataIndex]);
        const datasets = series.map((serie, index) => ({
            label: serie.label,
            data: serie.values,
            backgroundColor: token(serie.colorVar),
            hoverBackgroundColor: token(serie.colorVar),
            // 2px surface gap between stacked segments, rounded data end only
            borderColor: surface,
            borderWidth: stacked ? (ctx) => (isTop(ctx.datasetIndex, ctx.dataIndex) ? 0 : 2) : 0,
            borderSkipped: "start",
            borderRadius: stacked ? (ctx) => (isTop(ctx.datasetIndex, ctx.dataIndex) ? 4 : 0) : 4,
            maxBarThickness: 24,
            categoryPercentage: 0.7,
            barPercentage: 0.9,
            stack: stacked ? "s" : undefined,
        }));
        const valueAxis = {
            stacked,
            beginAtZero: true,
            reverse: horizontal && rtl,
            position: !horizontal && rtl ? "right" : undefined,
            grid: { color: token("--dd-grid"), lineWidth: 1, drawTicks: false },
            border: { display: false },
            ticks: {
                color: token("--dd-muted"),
                padding: 6,
                maxTicksLimit: 5,
                callback: (value) => this.formatAxis(value),
            },
        };
        const categoryAxis = {
            stacked,
            grid: { display: false },
            border: { color: token("--dd-axis") },
            // in Arabic, time and categories read right to left like the text
            reverse: !horizontal && rtl,
            ticks: { color: token("--dd-secondary"), autoSkip: !horizontal },
            position: horizontal ? (rtl ? "right" : "left") : undefined,
        };
        const multi = this.props.series.length > 1;
        this.chart = new window.Chart(canvas, {
            type: "bar",
            data: { labels: this.props.labels, datasets },
            options: {
                indexAxis: horizontal ? "y" : "x",
                maintainAspectRatio: false,
                // animate the first draw only: redraws after a filter change appear at once
                animation: firstDraw ? { duration: 250 } : false,
                font: { family: token("--dd-font") },
                layout: { padding: { top: 4, right: 8, left: 8 } },
                interaction: stacked ? { mode: "index", intersect: false } : { mode: "nearest", intersect: true, axis: horizontal ? "y" : "x" },
                scales: horizontal ? { x: valueAxis, y: categoryAxis } : { x: categoryAxis, y: valueAxis },
                plugins: {
                    legend: {
                        display: multi,
                        rtl,
                        align: "start",
                        labels: {
                            color: token("--dd-secondary"),
                            boxWidth: 10,
                            boxHeight: 10,
                            useBorderRadius: true,
                            borderRadius: 2,
                            padding: 14,
                        },
                    },
                    tooltip: {
                        rtl,
                        backgroundColor: token("--dd-tooltip-bg"),
                        titleColor: token("--dd-tooltip-ink"),
                        bodyColor: token("--dd-tooltip-ink"),
                        footerColor: token("--dd-tooltip-ink"),
                        padding: 10,
                        cornerRadius: 6,
                        boxPadding: 4,
                        usePointStyle: true,
                        callbacks: {
                            label: (ctx) => ` ${ctx.dataset.label}: ${this.formatValue(ctx.parsed[horizontal ? "x" : "y"])}`,
                            footer: (items) => {
                                if (!stacked || items.length < 2) {
                                    return "";
                                }
                                const total = items.reduce((sum, item) => sum + (item.parsed.y || 0), 0);
                                return `${_t("Total")}: ${this.formatValue(total)}`;
                            },
                        },
                    },
                },
            },
        });
    }
}

// ------------------------------------------------------------------------- heatmap
export class MedicalHeatmap extends Component {
    static template = "medical_dashboard.Heatmap";
    static props = { occupancy: Object };

    setup() {
        this.state = useState({ table: false });
    }

    get rows() {
        const { weekdays, hours, cells } = this.props.occupancy;
        return weekdays.map((weekday) => ({
            weekday,
            label: WEEKDAYS[weekday],
            cells: hours.map((hour) => cells.find((c) => c.weekday === weekday && c.hour === hour)),
        }));
    }

    hourLabel(hour) {
        return `${String(hour).padStart(2, "0")}`;
    }

    cellClass(cell) {
        if (!cell || cell.pct === null) {
            return "o_dd_heat_cell o_dd_heat_closed";
        }
        // 7 sequential steps; 0% stays on the lightest step
        const step = Math.min(6, Math.floor(cell.pct / (100 / 7)));
        return `o_dd_heat_cell o_dd_heat_${step}`;
    }

    cellTitle(row, cell) {
        if (!cell || cell.pct === null) {
            return `${row.label} ${this.hourLabel(cell?.hour ?? 0)}:00 · ${_t("Closed")}`;
        }
        return `${row.label} ${this.hourLabel(cell.hour)}:00 · ${formatNumber(cell.pct, 0)}% (${formatNumber(cell.hours, 1)} ${_t("room-hours")})`;
    }

    cellText(cell) {
        return !cell || cell.pct === null ? "—" : `${formatNumber(cell.pct, 0)}%`;
    }

    toggleTable() {
        this.state.table = !this.state.table;
    }
}

// ----------------------------------------------------------------------- dashboard
export class MedicalDashboard extends Component {
    static template = "medical_dashboard.Dashboard";
    static components = { MedicalChartCard, MedicalHeatmap };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ period: "this_month", doctorId: false, departmentId: false, data: null, loading: true });
        this.periods = [
            { key: "this_month", label: _t("This month") },
            { key: "last_month", label: _t("Last month") },
            { key: "last_90", label: _t("Last 90 days") },
            { key: "this_year", label: _t("This year") },
        ];
        onWillStart(async () => {
            await loadBundle("web.chartjs_lib");
            await this.load();
        });
    }

    async load() {
        this.state.loading = true;
        this.state.data = await this.orm.call("medical.dashboard", "get_dashboard_data", [], {
            period: this.state.period,
            doctor_id: this.state.doctorId || false,
            department_id: this.state.departmentId || false,
        });
        if (this.state.doctorId && !this.state.data.doctors.some((d) => d.id === this.state.doctorId)) {
            this.state.doctorId = false; // doctor not in the selected clinic
        }
        this.state.loading = false;
    }

    async setPeriod(key) {
        if (this.state.period !== key) {
            this.state.period = key;
            await this.load();
        }
    }

    async onDepartmentChange(ev) {
        this.state.departmentId = parseInt(ev.target.value) || false;
        await this.load();
    }

    async onDoctorChange(ev) {
        this.state.doctorId = parseInt(ev.target.value) || false;
        await this.load();
    }

    get currency() {
        return this.state.data.currency;
    }

    money(value) {
        return withCurrency(formatNumber(value, 0), this.currency);
    }

    moneyCompact(value) {
        return withCurrency(formatCompact(value), this.currency);
    }

    percent(value) {
        return `${formatNumber(value, 1)}%`;
    }

    delta(current, previous, upIsGood = true, isRate = false) {
        if (!previous && !current) {
            return null;
        }
        let change;
        let text;
        if (isRate) {
            change = current - previous;
            text = `${change >= 0 ? "+" : "−"}${formatNumber(Math.abs(change), 1)} ${_t("pts")}`;
        } else {
            if (!previous) {
                return null; // nothing to compare with
            }
            change = ((current - previous) / Math.abs(previous)) * 100;
            text = `${change >= 0 ? "+" : "−"}${formatNumber(Math.abs(change), 0)}%`;
        }
        if (Math.abs(change) < 0.05) {
            return { text: _t("no change"), cls: "o_dd_delta_flat", icon: "fa-minus" };
        }
        const good = change > 0 === upIsGood;
        return {
            text,
            cls: good ? "o_dd_delta_good" : "o_dd_delta_bad",
            icon: change > 0 ? "fa-arrow-up" : "fa-arrow-down",
        };
    }

    get kpiTiles() {
        const k = this.state.data.kpis;
        return [
            {
                key: "production",
                label: _t("Production"),
                hint: _t("Treatments performed, all payers"),
                value: this.moneyCompact(k.production),
                full: this.money(k.production),
                delta: this.delta(k.production, k.production_prev),
                hero: true,
            },
            {
                key: "collected",
                label: _t("Collected"),
                hint: _t("Payments received on medical invoices"),
                value: this.moneyCompact(k.collected),
                full: this.money(k.collected),
                delta: this.delta(k.collected, k.collected_prev),
            },
            {
                key: "visits",
                label: _t("Completed visits"),
                hint: `${formatNumber(k.new_patients)} ${_t("new patients")}`,
                value: formatNumber(k.visits),
                delta: this.delta(k.visits, k.visits_prev),
            },
            {
                key: "no_show",
                label: _t("No-show rate"),
                hint: _t("Booked patients who did not come"),
                value: this.percent(k.no_show_rate),
                delta: k.visits_prev ? this.delta(k.no_show_rate, k.no_show_rate_prev, false, true) : null,
            },
            {
                key: "utilization",
                label: _t("Room occupancy"),
                hint: _t("Booked share of opening hours"),
                value: this.percent(k.utilization),
                meter: Math.min(100, k.utilization),
            },
            {
                key: "rejection",
                label: _t("Insurance rejection rate"),
                hint: _t("Of settled claims, by amount"),
                value: this.percent(k.rejection_rate),
                meter: Math.min(100, k.rejection_rate),
                meterBad: true,
            },
        ];
    }

    get trendChart() {
        const rows = this.state.data.trend;
        const series = [
            { label: _t("Patient share"), values: rows.map((r) => r.patient), colorVar: "--dd-series-1" },
            { label: _t("Insurer share"), values: rows.map((r) => r.insurer), colorVar: "--dd-series-2" },
        ];
        if (rows.some((r) => r.programs)) {
            series.push({ label: _t("Care programs"), values: rows.map((r) => r.programs), colorVar: "--dd-series-3" });
        }
        return { labels: rows.map((r) => r.label), series };
    }

    get doctorChart() {
        const rows = this.state.data.by_doctor;
        return {
            labels: rows.map((r) => r.name),
            series: [{ label: _t("Production"), values: rows.map((r) => r.amount), colorVar: "--dd-series-1" }],
        };
    }

    get specialtyChart() {
        const rows = this.state.data.by_specialty;
        const byDepartment = this.state.data.breakdown === "department";
        return {
            title: byDepartment ? _t("Production by clinic") : _t("Production by specialty"),
            subtitle: byDepartment ? _t("Which clinics bring the revenue") : _t("Where this clinic's revenue comes from"),
            labels: rows.map((r) => r.name),
            series: [{ label: _t("Production"), values: rows.map((r) => r.amount), colorVar: "--dd-series-1" }],
        };
    }

    get insurerChart() {
        const rows = this.state.data.insurance.insurers;
        return {
            labels: rows.map((r) => r.name),
            series: [{ label: _t("Rejection rate"), values: rows.map((r) => r.rate), colorVar: "--dd-series-1" }],
        };
    }

    get leakageTotal() {
        return this.state.data.leakage.reduce((sum, item) => sum + (item.amount || 0), 0);
    }

    openLeak(item) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: item.action.name,
            res_model: item.action.res_model,
            domain: item.action.domain,
            views: [
                [false, "list"],
                [false, "form"],
            ],
            target: "current",
        });
    }
}

registry.category("actions").add("medical_dashboard.dashboard", MedicalDashboard);
