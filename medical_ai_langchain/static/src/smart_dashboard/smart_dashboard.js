/** @odoo-module **/
import { Component, markup, onWillStart, useRef, useState } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { formatCompact, formatNumber, MedicalChartCard } from "@medical_dashboard/dashboard/dashboard";

const SEVERITY = {
    critical: { icon: "fa-exclamation-circle", label: _t("Critical") },
    warning: { icon: "fa-exclamation-triangle", label: _t("Warning") },
    info: { icon: "fa-info-circle", label: _t("Info") },
};

function delta(now, before, higherIsGood = true) {
    if (!before) {
        return null;
    }
    const pct = ((now - before) / before) * 100;
    if (Math.abs(pct) < 0.5) {
        return { text: _t("no change"), cls: "o_dd_delta_flat", icon: "" };
    }
    const up = pct > 0;
    return {
        text: `${up ? "+" : "−"}${formatNumber(Math.abs(pct), 0)}%`,
        cls: up === higherIsGood ? "o_dd_delta_good" : "o_dd_delta_bad",
        icon: up ? "fa-arrow-up" : "fa-arrow-down",
    };
}

export class MedicalSmartDashboard extends Component {
    static template = "medical_ai_langchain.SmartDashboard";
    static components = { MedicalChartCard };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.inputRef = useRef("question");
        this.chatRef = useRef("chat");
        this.state = useState({
            days: 30,
            data: null,
            loading: true,
            insightPeriod: "this_month",
            insightBusy: false,
            indexBusy: false,
            sessionId: false,
            messages: [],
            asking: false,
            question: "",
        });
        this.dayOptions = [
            { key: 7, label: _t("7 days") },
            { key: 30, label: _t("30 days") },
            { key: 90, label: _t("90 days") },
        ];
        this.insightPeriods = [
            { key: "this_month", label: _t("This month") },
            { key: "last_month", label: _t("Last month") },
            { key: "last_90", label: _t("Last 90 days") },
            { key: "this_year", label: _t("This year") },
        ];
        this.suggestions = [
            _t("Who is booked today, by clinic?"),
            _t("What clinical work is overdue?"),
            _t("Which claim rejections are most frequent?"),
            _t("What does the insurer policy say about pre-authorization?"),
        ];
        onWillStart(async () => {
            await loadBundle("web.chartjs_lib");
            await this.load();
        });
    }

    async load() {
        this.state.loading = true;
        const data = await this.orm.call("medical.ai.smart.dashboard", "get_data", [], { days: this.state.days });
        this.state.data = data;
        if (!this.state.sessionId) {
            this.state.sessionId = data.session.id;
            this.state.messages = data.session.messages.map((m) => this.prepare(m));
        }
        this.state.loading = false;
    }

    prepare(message) {
        return { ...message, html: markup(message.html || "") };
    }

    async setDays(days) {
        if (this.state.days !== days) {
            this.state.days = days;
            await this.load();
        }
    }

    // ------------------------------------------------------------------ tiles
    get tiles() {
        const u = this.state.data.usage;
        const p = this.state.data.usage_prev;
        const tokens = (u.input_tokens || 0) + (u.output_tokens || 0);
        return [
            {
                key: "requests", label: _t("AI requests"), value: formatNumber(u.requests),
                delta: delta(u.requests, p.requests), hint: _t("vs previous period"),
            },
            {
                key: "success", label: _t("Success rate"),
                value: u.success_rate === null ? "—" : `${formatNumber(u.success_rate, 1)}%`,
                meter: u.success_rate === null ? undefined : u.success_rate,
                hint: _t("answered without error"),
            },
            {
                key: "tokens", label: _t("Tokens"), value: formatCompact(tokens),
                hint: `${formatCompact(u.input_tokens)} ${_t("in")} · ${formatCompact(u.output_tokens)} ${_t("out")}`,
            },
            {
                key: "cost", label: _t("Estimated cost"), value: `$${formatNumber(u.cost, u.cost < 10 ? 2 : 0)}`,
                delta: delta(u.cost, p.cost, false), hint: _t("from the prices in Settings"),
            },
            {
                key: "latency", label: _t("Average response"),
                value: u.avg_duration === null ? "—" : `${formatNumber(u.avg_duration, 1)} ${_t("s")}`,
                hint: _t("per request"),
            },
            {
                key: "questions", label: _t("Questions asked"), value: formatNumber(u.questions),
                delta: delta(u.questions, p.questions), hint: _t("assistant and knowledge base"),
            },
        ];
    }

    get dailyChart() {
        const d = this.state.data.daily;
        return {
            labels: d.labels,
            series: [
                { label: _t("Answered"), values: d.ok, colorVar: "--dd-series-1" },
                { label: _t("Errors"), values: d.error, colorVar: "--dd-critical" },
            ],
        };
    }

    get featureChart() {
        const f = this.state.data.by_feature;
        return {
            labels: f.labels,
            series: [
                { label: _t("Answered"), values: f.ok, colorVar: "--dd-series-1" },
                { label: _t("Errors"), values: f.error, colorVar: "--dd-critical" },
            ],
        };
    }

    get engineChips() {
        const e = this.state.data.engine;
        const chips = [
            { icon: "fa-cogs", text: e.engine === "langchain" ? "LangChain" : _t("Direct HTTP") },
            { icon: "fa-plug", text: e.configured ? `${e.provider} · ${e.model}` : _t("No AI provider set") },
            { icon: "fa-database", text: `${e.embedding} · ${e.vector_store === "pgvector" ? "pgvector" : _t("table")}` },
        ];
        if (e.anonymize) {
            chips.push({ icon: "fa-user-secret", text: _t("Patients anonymized") });
        }
        return chips;
    }

    severity(key) {
        return SEVERITY[key] || SEVERITY.info;
    }

    get kbMax() {
        return Math.max(1, ...this.state.data.kb.by_category.map((c) => c.count));
    }

    // --------------------------------------------------------------- insights
    async generateInsights() {
        this.state.insightBusy = true;
        try {
            this.state.data.insight = await this.orm.call("medical.ai.smart.dashboard", "generate_insights", [], {
                period: this.state.insightPeriod,
            });
        } finally {
            this.state.insightBusy = false;
        }
        await this.load();
    }

    onInsightPeriod(ev) {
        this.state.insightPeriod = ev.target.value;
    }

    // ---------------------------------------------------------------- knowledge
    async indexPending() {
        this.state.indexBusy = true;
        try {
            await this.orm.call("medical.ai.smart.dashboard", "index_pending", []);
            this.notification.add(_t("Knowledge base indexed."), { type: "success" });
        } finally {
            this.state.indexBusy = false;
        }
        await this.load();
    }

    openKnowledge() {
        this.action.doAction("medical_ai_langchain.action_medical_ai_kb_document");
    }

    openDocument(id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "medical.ai.kb.document",
            res_id: id,
            views: [[false, "form"]],
        });
    }

    openLog() {
        this.action.doAction("medical_ai.action_medical_ai_log");
    }

    // ----------------------------------------------------------------- chat
    onInput(ev) {
        this.state.question = ev.target.value;
    }

    onKeydown(ev) {
        if (ev.key === "Enter" && !ev.shiftKey) {
            ev.preventDefault();
            this.ask();
        }
    }

    async ask(text) {
        const question = (text || this.state.question || "").trim();
        if (!question || this.state.asking) {
            return;
        }
        this.state.question = "";
        this.state.messages.push({ id: `tmp${Date.now()}`, role: "user", html: markup(""), text: question });
        this.state.asking = true;
        this.scrollChat();
        try {
            const result = await this.orm.call("medical.ai.chat.session", "ask_from_dashboard", [question], {
                session_id: this.state.sessionId || false,
            });
            this.state.sessionId = result.session_id;
            this.state.messages.push(this.prepare(result.message));
        } catch (error) {
            this.state.messages.pop();
            this.state.question = question;
            throw error;
        } finally {
            this.state.asking = false;
            this.scrollChat();
        }
    }

    newConversation() {
        this.state.sessionId = false;
        this.state.messages = [];
    }

    scrollChat() {
        requestAnimationFrame(() => {
            const el = this.chatRef.el;
            if (el) {
                el.scrollTop = el.scrollHeight;
            }
        });
    }
}

registry.category("actions").add("medical_ai_langchain.smart_dashboard", MedicalSmartDashboard);
