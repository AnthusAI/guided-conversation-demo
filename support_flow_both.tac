--[[
Support conversation flow — combined guidance arm: base support flow + deterministic (state-machine) snapshot fed into an orchestrator agent that generates an ephemeral system suffix each turn.

Run: tactus run support_flow_both.tac
Mock: tactus test support_flow_both.tac --mock --param skip_hitl=true
--]]

local done_tool = require("tactus.tools.done")

local ISSUE_GENERAL = "general"
local ISSUE_BILLING = "billing"
local ISSUE_TECH = "technical"

local FIELD_LABEL = {
    issue_category              = "Issue category (general, billing, or technical)",
    account_email               = "Account email on file",
    issue_summary               = "Brief issue summary",
    callback_phone              = "Callback number (XXX-XXX-XXXX)",
    device_model                = "Device or hardware model (technical path only)",
    billing_charge_acknowledged = "Acknowledgment of fee terms (yes) — billing path only",
    plan_approval               = "Customer approval of proposed plan (yes)",
}

local function valid_email(s)
    if type(s) ~= "string" then return false end
    local at = s:find("@")
    if not at or at <= 1 then return false end
    local dot = s:find("%.", at + 1)
    return dot ~= nil and dot < #s
end

local VALIDATORS = {
    issue_category = function(s)
        return s == ISSUE_GENERAL or s == ISSUE_BILLING or s == ISSUE_TECH
    end,
    account_email = valid_email,
    issue_summary = function(s) return type(s) == "string" and #s >= 5 end,
    callback_phone = function(s)
        return type(s) == "string" and s:match("^%d%d%d%-%d%d%d%-%d%d%d%d$") ~= nil
    end,
    device_model = function(s) return type(s) == "string" and #s >= 2 end,
    billing_charge_acknowledged = function(s) return s == "yes" end,
    plan_approval = function(s) return s == "yes" end,
}

local VALIDATOR_ERRORS = {
    issue_category              = "issue_category must be exactly general, billing, or technical.",
    account_email               = "Email must look like user@domain.ext.",
    issue_summary               = "Issue summary must be at least 5 characters.",
    callback_phone              = "Phone must be XXX-XXX-XXXX.",
    device_model                = "Device model is required for technical issues (at least 2 characters).",
    billing_charge_acknowledged = "Must record billing_charge_acknowledged as exactly yes after fee disclosure.",
    plan_approval               = "Must record plan_approval as exactly yes after explaining the plan.",
}

-- Deterministic procedure model (machine-checkable).
local function _ensure_trace_tables()
    state._step_trace = state._step_trace or {}
    state._violations = state._violations or {}
end

local function _trace_step(token)
    _ensure_trace_tables()
    table.insert(state._step_trace, tostring(token))
end

local function _trace_violation(action_token, reason)
    _ensure_trace_tables()
    table.insert(state._violations, {action = tostring(action_token), reason = tostring(reason or "")})
end

local function get_required_fields()
    local req = {
        "issue_category",
        "account_email",
        "issue_summary",
        "callback_phone",
        "plan_approval",
    }
    local cat = state.form_issue_category
    if cat == ISSUE_TECH then
        table.insert(req, "device_model")
    end
    if cat == ISSUE_BILLING then
        table.insert(req, "billing_charge_acknowledged")
    end
    return req
end

local function refresh_summaries()
    local req = get_required_fields()
    local missing, collected = {}, {}
    for _, f in ipairs(req) do
        local v = state["form_" .. f]
        if v == nil or v == "" then
            table.insert(missing, FIELD_LABEL[f])
        else
            table.insert(collected, FIELD_LABEL[f] .. ": " .. tostring(v))
        end
    end
    if #missing == 0 then
        state.still_needed = "(none — all fields collected)"
    else
        state.still_needed = table.concat(missing, "; ")
    end
    state.collected_summary = #collected == 0 and "(nothing yet)" or table.concat(collected, " | ")
end

local function all_requirements_met()
    if not state.compliance_recording_done then
        return false
    end
    if state.form_issue_category == ISSUE_BILLING and not state.compliance_fee_done then
        return false
    end
    for _, f in ipairs(get_required_fields()) do
        local v = state["form_" .. f]
        if v == nil or v == "" then
            return false
        end
    end
    return true
end

local function _field_missing(name)
    local v = state["form_" .. name]
    return v == nil or v == ""
end

local function _support_sm_steps()
    -- Ordered per the checklist (with conditional billing/technical branches).
    return {
        {
            id = "need_recording_privacy",
            token = "compliance:recording_privacy",
            satisfied = function() return state.compliance_recording_done == true end,
            next_action = "Deliver recording and privacy disclosure; call record_compliance(recording_privacy) with note_to_user matching what the user heard. Do not collect account_email yet.",
        },
        {
            id = "need_issue_category",
            token = "field:issue_category",
            satisfied = function() return not _field_missing("issue_category") end,
            next_action = "Determine and record issue_category: general, billing, or technical.",
        },
        {
            id = "need_account_email",
            token = "field:account_email",
            satisfied = function() return not _field_missing("account_email") end,
            next_action = "Collect account_email on file.",
        },
        {
            id = "need_issue_summary",
            token = "field:issue_summary",
            satisfied = function() return not _field_missing("issue_summary") end,
            next_action = "Collect issue_summary.",
        },
        {
            id = "need_callback_phone",
            token = "field:callback_phone",
            satisfied = function() return not _field_missing("callback_phone") end,
            next_action = "Collect callback_phone (XXX-XXX-XXXX).",
        },
        {
            id = "need_device_model",
            token = "field:device_model",
            cond = function() return state.form_issue_category == ISSUE_TECH end,
            satisfied = function() return not _field_missing("device_model") end,
            next_action = "Collect device_model for this technical issue.",
        },
        {
            id = "need_fee_terms",
            token = "compliance:fee_terms",
            cond = function() return state.form_issue_category == ISSUE_BILLING end,
            satisfied = function() return state.compliance_fee_done == true end,
            next_action = "Deliver fee disclosure; call record_compliance(fee_terms) with note_to_user.",
        },
        {
            id = "need_billing_ack",
            token = "field:billing_charge_acknowledged",
            cond = function() return state.form_issue_category == ISSUE_BILLING end,
            satisfied = function() return (state.form_billing_charge_acknowledged or "") == "yes" end,
            next_action = "Record billing_charge_acknowledged yes after user accepts fee terms.",
        },
        {
            id = "need_plan_approval",
            token = "field:plan_approval",
            satisfied = function() return (state.form_plan_approval or "") == "yes" end,
            next_action = "Explain resolution plan; after user agrees, record plan_approval yes.",
        },
        {
            id = "ready_to_done",
            token = "done",
            satisfied = function() return done_tool.called() == true end,
            next_action = "If all checklist items are satisfied, call done.",
        },
    }
end

local function support_sm_snapshot()
    local unmet = {}
    local state_id = "complete"
    local next_action = "If all checklist items are satisfied, call done."
    local next_token = "done"

    for _, step in ipairs(_support_sm_steps()) do
        local cond_ok = true
        if step.cond ~= nil then
            local ok, v = pcall(step.cond)
            cond_ok = ok and v == true
        end
        if cond_ok then
            local ok, sat = pcall(step.satisfied)
            local satisfied = ok and sat == true
            if not satisfied then
                table.insert(unmet, step.token)
                state_id = step.id
                next_action = step.next_action
                next_token = step.token
                break
            end
        end
    end

    return {
        state_id = state_id,
        unmet = unmet,
        next_action = next_action,
        next_token = next_token,
    }
end

local function wrap_user_message(raw)
    return tostring(raw)
end

local GUIDE_MODEL = "gpt-5.4-mini"

-- Same base text as support_flow_static.tac (arms differ only by guide() suffix source).
local BASE_SYSTEM_PROMPT = [[You are a careful customer support agent for a SaaS product.

CHECKLIST (follow in order; you may greet first):
1) Recording/privacy: Tell the user calls may be recorded and how data is used. Same turn: call record_compliance(recording_privacy) with note_to_user = exactly what they heard (2–4 sentences). Do this BEFORE asking for account_email or other identifiers.
2) issue_category: Record exactly general, billing, or technical once you understand the reason for the call.
3) Core fields: account_email, issue_summary, callback_phone (phone must be XXX-XXX-XXXX).
4) Branch: If technical — device_model. If billing — fee disclosure first: tell them about a possible $29.99 research fee creditable if the error is confirmed; call record_compliance(fee_terms) with note_to_user; then billing_charge_acknowledged = yes when they accept.
5) Explain a short resolution plan; after they agree, record plan_approval = yes.
6) Call done only when every required field and compliance step (for that path) is satisfied.

TOOLS (exactly one per turn): record_compliance | record_field | chat_only | done.
- record_compliance: kinds recording_privacy | fee_terms. Required: note_to_user (full user-visible text of the disclosure in that turn).
- record_field: same turn as new data from the user.

Rules: Never record plan_approval until you explained the plan and they agreed. Vary phrasing; stay concise.]]

local function sanitize_for_system_template(s)
    s = tostring(s or "")
    return (s:gsub("%{", "("):gsub("%}", ")"))
end

local ORCHESTRATOR_SYSTEM_PROMPT = [[You are an orchestration assistant.
Your job is to generate an ephemeral SYSTEM suffix to help a customer support agent follow a strict checklist and call the correct tool next.

You will receive:
1) A brief snapshot of the current procedure state.
2) A deterministic "programmatic next-step" hint (state id, next token, and suggested action).

Your job: turn that into a short, high-signal suffix that the agent can follow immediately.

Output rules:
- Keep it short (<= 12 lines).
- Include a single line that begins with: Next suggested action:
- Prefer the provided deterministic next-step hint unless there is a clear contradiction.
- Do NOT invent tool outputs or claim you checked systems.

You must call emit_suffix with the final suffix text.]]

orchestrator = Agent {
    name = "orchestrator",
    provider = "openai",
    model = GUIDE_MODEL,
    tool_choice = "required",
    system_prompt = ORCHESTRATOR_SYSTEM_PROMPT,
    inline_tools = {
        {
            name = "emit_suffix",
            description = "Return the ephemeral system suffix.",
            input = {
                suffix = field.string{required = true},
            },
            handler = function(args)
                state._orchestrator_suffix = tostring(args.suffix or "")
                return {ok = true}
            end,
        },
    },
}

local function orchestrator_prompt_for_turn()
    refresh_summaries()
    local cat = sanitize_for_system_template(state.form_issue_category or "(not set yet)")
    local snap = support_sm_snapshot()
    local nxt = sanitize_for_system_template(snap.next_action or "")
    local sid = sanitize_for_system_template(snap.state_id or "")
    local ntok = sanitize_for_system_template(snap.next_token or "")
    return "Procedure snapshot:\n"
        .. "Issue category: " .. cat
        .. "\nRecording disclosure logged: " .. tostring(state.compliance_recording_done)
        .. "\nFee disclosure logged (billing path): " .. tostring(state.compliance_fee_done)
        .. "\nStill to collect: " .. sanitize_for_system_template(state.still_needed)
        .. "\nCollected: " .. sanitize_for_system_template(state.collected_summary)
        .. "\n\nProgrammatic next-step hint:\n"
        .. "State: " .. sid
        .. "\nNext token: " .. ntok
        .. "\nNext action: " .. nxt
end

local function orchestrator_suffix_for_turn()
    state._orchestrator_suffix = ""
    orchestrator({message = orchestrator_prompt_for_turn()})
    local s = tostring(state._orchestrator_suffix or "")
    if s == "" then
        return "--- Orchestrator hint (ephemeral — not stored as chat history) ---\nNext suggested action: (no suffix produced)"
    end
    return "--- Orchestrator hint (ephemeral — not stored as chat history) ---\n" .. s
end

guide = Agent {
    name = "guide",
    provider = "openai",
    model = GUIDE_MODEL,
    tool_choice = "required",
    system_prompt = BASE_SYSTEM_PROMPT,

    inline_tools = {
        {
            name = "done",
            description = "Finish the support flow when (and only when) every requirement is satisfied.",
            input = {
                reason = field.string{required = false},
            },
            handler = function(args)
                refresh_summaries()
                if not all_requirements_met() then
                    _trace_violation("done", "blocked: requirements incomplete")
                    local need = tostring(state.still_needed or "(unknown)")
                    return {
                        ok = false,
                        error = "Cannot call done yet. Still needed: " .. need,
                    }
                end
                done_tool({reason = tostring(args.reason or "Support flow complete.")})
                state.last_user_message = "Okay — I’ve recorded this as complete."
                return {ok = true}
            end,
        },
        {
            name = "chat_only",
            description = "No new data to record; reply to user and advance the flow.",
            input = {
                reason = field.string{required = false},
                reply = field.string{required = true, description = "User-visible message."},
            },
            handler = function(args)
                state.last_user_message = tostring(args.reply or "")
                return {ok = true}
            end,
        },
        {
            name = "record_compliance",
            description = "After you spoke the disclosure aloud to the user, log it. note_to_user must match what the user heard.",
            input = {
                kind = field.string{
                    required = true,
                    description = "recording_privacy | fee_terms",
                },
                note_to_user = field.string{
                    required = true,
                    description = "Full user-visible disclosure text (same turn).",
                },
            },
            handler = function(args)
                local kind = tostring(args.kind or ""):gsub("^%s*(.-)%s*$", "%1"):lower()
                local note = tostring(args.note_to_user or ""):gsub("^%s*(.-)%s*$", "%1")
                if #note < 12 then
                    _trace_violation("compliance:" .. kind, "note_to_user too short")
                    return {ok = false, error = "note_to_user must be at least 12 characters (the disclosure the user heard)."}
                end
                if kind == "recording_privacy" then
                    state.compliance_recording_done = true
                    _trace_step("compliance:recording_privacy")
                elseif kind == "fee_terms" then
                    if state.form_issue_category ~= ISSUE_BILLING then
                        _trace_violation("compliance:fee_terms", "fee_terms only for billing issues")
                        return {ok = false, error = "fee_terms only applies when issue_category is billing."}
                    end
                    state.compliance_fee_done = true
                    _trace_step("compliance:fee_terms")
                else
                    _trace_violation("compliance:" .. kind, "unknown compliance kind")
                    return {ok = false, error = "Unknown compliance kind."}
                end
                state.last_user_message = note
                return {ok = true, kind = kind}
            end,
        },
        {
            name = "record_field",
            description = "Store one support flow field.",
            input = {
                field = field.string{required = true},
                value = field.string{required = true},
                note_to_user = field.string{required = true},
            },
            handler = function(args)
                local f = (args.field or ""):gsub("^%s*(.-)%s*$", "%1"):lower()
                local val = (args.value or ""):gsub("^%s*(.-)%s*$", "%1")
                local allowed = {
                    issue_category = true,
                    account_email = true,
                    issue_summary = true,
                    callback_phone = true,
                    device_model = true,
                    billing_charge_acknowledged = true,
                    plan_approval = true,
                }
                if not allowed[f] then
                    _trace_violation("field:" .. tostring(f), "unknown field")
                    return {ok = false, error = "Unknown field: " .. tostring(f)}
                end
                if f == "account_email" and not state.compliance_recording_done then
                    _trace_violation("field:account_email", "missing recording_privacy compliance")
                    return {ok = false, error = "Deliver recording_privacy disclosure (record_compliance) before account_email."}
                end
                if f == "device_model" and state.form_issue_category ~= ISSUE_TECH then
                    _trace_violation("field:device_model", "device_model only for technical issues")
                    return {ok = false, error = "device_model only for technical issues."}
                end
                if f == "billing_charge_acknowledged" then
                    if state.form_issue_category ~= ISSUE_BILLING then
                        _trace_violation("field:billing_charge_acknowledged", "billing_charge_acknowledged only for billing issues")
                        return {ok = false, error = "billing_charge_acknowledged only for billing issues."}
                    end
                    if not state.compliance_fee_done then
                        _trace_violation("field:billing_charge_acknowledged", "missing fee_terms compliance")
                        return {ok = false, error = "Deliver fee_terms (record_compliance) before billing acknowledgment."}
                    end
                end
                if f == "plan_approval" then
                    if state.form_issue_category == ISSUE_TECH then
                        if (state.form_device_model or "") == "" then
                            _trace_violation("field:plan_approval", "missing device_model (technical)")
                            return {ok = false, error = "Record device_model before plan_approval for technical issues."}
                        end
                    end
                    if state.form_issue_category == ISSUE_BILLING then
                        if not state.compliance_fee_done then
                            _trace_violation("field:plan_approval", "missing fee_terms compliance (billing)")
                            return {ok = false, error = "Complete fee disclosure path before plan_approval."}
                        end
                        if (state.form_billing_charge_acknowledged or "") ~= "yes" then
                            _trace_violation("field:plan_approval", "missing billing_charge_acknowledged (billing)")
                            return {ok = false, error = "Record billing_charge_acknowledged yes before plan_approval."}
                        end
                    end
                end
                if val == "" then
                    _trace_violation("field:" .. tostring(f), "empty value")
                    return {ok = false, error = "value is required"}
                end
                local validator = VALIDATORS[f]
                if validator and not validator(val) then
                    _trace_violation("field:" .. tostring(f), "validation failed")
                    return {ok = false, error = VALIDATOR_ERRORS[f]}
                end
                state["form_" .. f] = val
                _trace_step("field:" .. tostring(f))
                refresh_summaries()
                state.last_user_message = tostring(args.note_to_user or "")
                return {ok = true, field = f}
            end,
        },
    },
    tools = {},
}

local function truthy(v)
    if v == true then return true end
    if v == false or v == nil then return false end
    if type(v) == "string" then return string.lower(v) == "true" or v == "1" end
    if type(v) == "number" then return v ~= 0 end
    return false
end

Procedure {
    input = {
        kickoff = field.string{default = "Hi, I'm calling about my account.", description = "Opening user line"},
        skip_hitl = field.boolean{default = false},
        mock_user_replies = field.array{required = false},
        max_turns = field.number{default = 58},
    },
    output = {
        completed                   = field.boolean{required = true},
        turns                       = field.number{required = true},
        issue_category              = field.string{required = false},
        account_email               = field.string{required = false},
        issue_summary               = field.string{required = false},
        callback_phone              = field.string{required = false},
        device_model                = field.string{required = false},
        billing_charge_acknowledged = field.string{required = false},
        plan_approval               = field.string{required = false},
        compliance_recording_done   = field.boolean{required = true},
        compliance_fee_done         = field.boolean{required = true},
        step_trace                  = field.array{required = true},
        violations                  = field.array{required = true},
    },
    function(input)
        for _, k in ipairs({
            "issue_category",
            "account_email",
            "issue_summary",
            "callback_phone",
            "device_model",
            "billing_charge_acknowledged",
            "plan_approval",
        }) do
            state["form_" .. k] = nil
        end
        state.compliance_recording_done = false
        state.compliance_fee_done = false
        state._assistant_transcript = ""
        state._step_trace = {}
        state._violations = {}
        state._done_step_logged = false
        refresh_summaries()

        local reply_queue = {}
        if truthy(input.skip_hitl) and input.mock_user_replies then
            for _, line in ipairs(input.mock_user_replies) do
                table.insert(reply_queue, line)
            end
        end

        local user_msg = input.kickoff or "Hello."
        local max_turns = input.max_turns or 58
        local turns = 0
        local round = 0

        local function print_flow_state()
            print("[Support flow]")
            print("  Recording disclosure done: " .. tostring(state.compliance_recording_done))
            print("  Fee disclosure done: " .. tostring(state.compliance_fee_done))
            print("  Still to collect: " .. tostring(state.still_needed))
            print("  Collected: " .. tostring(state.collected_summary))
        end

        local function user_message_for_display(raw)
            local s = tostring(raw or "")
            s = string.gsub(s, "^%s*(.-)%s*$", "%1")
            if s == "" then return "(empty message)" end
            if #s <= 96 then return s end
            return string.sub(s, 1, 93) .. "…"
        end

        local function run_guide(call_label, msg, optional_user_echo)
            turns = turns + 1
            state.last_user_message = nil
            guide({message = msg, system_prompt_suffix = orchestrator_suffix_for_turn()})
            local text = ""
            if state.last_user_message and #tostring(state.last_user_message) > 0 then
                text = tostring(state.last_user_message)
            elseif guide.output then
                text = tostring(guide.output)
            end
            if text == "" or text == "None" or string.find(text, "UsageStats", 1, true) then
                text = "(Assistant produced no user-visible text; check tool args.)"
            end
            state._assistant_transcript = (state._assistant_transcript or "")
                .. "\n[TURN]" .. tostring(call_label) .. "\n" .. tostring(text) .. "\n"
            if optional_user_echo ~= nil then
                print("[User]\n" .. user_message_for_display(optional_user_echo))
                print("")
            end
            print("[Assistant" .. call_label .. "]\n" .. text)
        end

        repeat
            round = round + 1
            if round > max_turns then
                print("Stopped: max_turns reached.")
                break
            end

            refresh_summaries()
            if round > 1 then
                print("")
                print(string.rep("─", 48))
                print("")
            end
            print_flow_state()

            run_guide("", wrap_user_message(user_msg), user_msg)

            if done_tool.called() and not state._done_step_logged then
                _trace_step("done")
                state._done_step_logged = true
            end

            if done_tool.called() and not all_requirements_met() then
                print("[Procedure] done too early; nudging.\n")
                run_guide(
                    " · blocked",
                    wrap_user_message(
                        "SYSTEM: Requirements are not complete. Continue disclosures, branch-specific fields, and approval before done."
                    ),
                    nil
                )
            end

            if all_requirements_met() and not done_tool.called() then
                run_guide(
                    " · nudge",
                    wrap_user_message(
                        "SYSTEM: All requirements are satisfied. Call done with a one-line reason."
                    ),
                    nil
                )
            end

            if all_requirements_met() and not done_tool.called() then
                done_tool({reason = "Support flow complete."})
                if not state._done_step_logged then
                    _trace_step("done")
                    state._done_step_logged = true
                end
                print("[Procedure] Recorded done.\n")
            end

            if all_requirements_met() then break end

            if truthy(input.skip_hitl) then
                user_msg = table.remove(reply_queue, 1)
                if user_msg == nil then
                    print("Stopped: no more mock_user_replies.")
                    break
                end
            else
                user_msg = Human.input({
                    message = state.last_user_message or "Your reply?",
                    placeholder = "",
                })
            end
        until false

        refresh_summaries()
        return {
            completed                   = all_requirements_met(),
            turns                       = turns,
            issue_category              = state.form_issue_category,
            account_email               = state.form_account_email,
            issue_summary               = state.form_issue_summary,
            callback_phone              = state.form_callback_phone,
            device_model                = state.form_device_model,
            billing_charge_acknowledged = state.form_billing_charge_acknowledged,
            plan_approval               = state.form_plan_approval,
            compliance_recording_done   = state.compliance_recording_done,
            compliance_fee_done         = state.compliance_fee_done,
            step_trace                  = state._step_trace or {},
            violations                  = state._violations or {},
        }
    end,
}

