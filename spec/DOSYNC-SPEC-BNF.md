# DoSync Protocol — Formal Grammar (BNF)

**Status:** Specification supplement  
**Version:** 0.1  
**Applies to:** DoSync Protocol v0.1  
**Location:** `spec/DOSYNC-SPEC-BNF.md`

This document defines the formal grammar for all DoSync protocol messages using Extended Backus-Naur Form (EBNF). It is a normative supplement to `DOSYNC-SPEC-v0.1.md` — in case of conflict, this document takes precedence for message structure.

---

## Notation

```
::=     definition
|       alternative
[ ]     optional (0 or 1)
{ }     repetition (0 or more)
( )     grouping
" "     literal string (terminal)
< >     non-terminal
```

**Important:** JSON structural characters (object braces, array brackets, colons, commas) appear as terminals in quotes: `"{"`, `"}"`, `"["`, `"]"`, `":"`, `","`. Unquoted `{ }` always means EBNF repetition.

---

## 1. Primitive types

```ebnf
<digit>         ::= "0" | "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9"
<hex-digit>     ::= <digit> | "a" | "b" | "c" | "d" | "e" | "f"
                  | "A" | "B" | "C" | "D" | "E" | "F"
<char>          ::= (* any Unicode character except '"' and '\' *)
                  | "\" ( '"' | "\" | "/" | "n" | "r" | "t" | "u" <hex-digit>{4} )

<string>        ::= DQUOTE { <char> } DQUOTE   (* DQUOTE = U+0022 *)
<integer>       ::= <digit> { <digit> }
<float>         ::= [ "-" ] <integer> [ "." <integer> ]
<boolean>       ::= "true" | "false"
<null>          ::= "null"
<timestamp>     ::= <float>              (* Unix timestamp, seconds since epoch — always positive *)
<uuid>          ::= <string>             (* format: hex{8}-hex{4}-hex{4}-hex{4}-hex{12} *)
<semver>        ::= <string>             (* format: major.minor.patch *)

<value>         ::= <string> | <integer> | <float> | <boolean> | <null>
                  | <object> | <array>
<array>         ::= "[" [ <value> { "," <value> } ] "]"
<object>        ::= "{" [ <kv-pair> { "," <kv-pair> } ] "}"
<kv-pair>       ::= <string> ":" <value>
```

---

## 2. Enumerated types

```ebnf
<intent-class>  ::= "ensure_safety"
                  | "alert_anomaly"
                  | "control_access"
                  | "monitor_health"
                  | "notify_family"
                  | "report_status"
                  | "set_environment"
                  | "save_energy"
                  | "remind_chore"
                  | "bedtime_routine"
                  | "morning_routine"
                  | "away_mode"
                  | "children_arrived_home"

<urgency>       ::= "emergency" | "alert" | "info"

<severity>      ::= "critical" | "warning" | "info"

<adapter-type>  ::= "wiz" | "homeassistant" | "gpio" | "mqtt"
                  | "simulated" | "notifications"
                  | <string>   (* vendor extension — any reverse-DNS string *)

<source>        ::= "mcp" | "api" | "gpio" | "scheduler"
                  | "mqtt" | "websocket" | <string>
```

---

## 3. Capability Manifest

```ebnf
<capability-manifest> ::= "{"
    "device_id"     ":" <string> ","
    "device_name"   ":" <string> ","
    "tags"          ":" <tag-list> ","
    "actuators"     ":" <actuator-list> ","
    "sensors"       ":" <sensor-list> ","
    "emergency_capable" ":" <boolean> ","
    "adapter"       ":" <adapter-type> ","
    "adapter_config" ":" <object>   (* MUST be redacted in GET /v1/devices/:id responses — see S17 *)
    [ "," "location"   ":" <string> ]
    [ "," "firmware"   ":" <semver> ]
    [ "," "cert_tier"  ":" ( "basic" | "standard" | "emergency" ) ]
  "}"

<tag-list>      ::= "[" [ <string> { "," <string> } ] "]"

<actuator-list> ::= "[" [ <actuator-spec> { "," <actuator-spec> } ] "]"
<actuator-spec> ::= "{"
    "type"    ":" <string> ","
    "description" ":" <string>
  "}"

<sensor-list>   ::= "[" [ <sensor-spec> { "," <sensor-spec> } ] "]"
<sensor-spec>   ::= "{"
    "type"    ":" <string> ","
    "unit"    ":" <string>
  "}"
```

---

## 4. Intent

```ebnf
<intent-request> ::= "{"
    "intent"    ":" <intent-class> ","
    "urgency"   ":" <urgency> ","
    "source"    ":" <source> ","
    "context"   ":" <object>
  "}"

<intent-response> ::= "{"
    "intent_id" ":" <uuid> ","
    "status"    ":" ( "accepted" | "blocked" | "partial" | "executed" ) ","
    "urgency"   ":" <urgency>
    [ "," "actions_count" ":" <integer> ]
    [ "," "message"       ":" <string> ]
  "}"
```

---

## 5. Action Plan

```ebnf
<action-plan>   ::= "{"
    "intent_id" ":" <uuid> ","
    "urgency"   ":" <urgency> ","
    "actions"   ":" <action-list>
  "}"

<action-list>   ::= "[" [ <device-action> { "," <device-action> } ] "]"

<device-action> ::= "{"
    "device_id"       ":" <string> ","
    "action"          ":" <string> ","
    "params"          ":" <object> ","
    "relevance_score" ":" <float>   (* range: 0.0 to 100.0 *)
  "}"
```

---

## 6. Device Event

```ebnf
<event-request> ::= "{"
    "device_id" ":" <string> ","
    "event_type" ":" <string> ","
    "severity"  ":" <severity> ","
    "data"      ":" <object>
    [ "," "timestamp" ":" <timestamp> ]
  "}"

<event-response> ::= "{"
    "status"    ":" "accepted" ","
    "event_id"  ":" <uuid>
  "}"
```

---

## 7. Audit Log Entry

```ebnf
<audit-entry>   ::= "{"
    "entry_id"  ":" <integer> ","
    "timestamp" ":" <timestamp> ","
    "type"      ":" <audit-type> ","
    "intent_id" ":" ( <uuid> | <null> ) ","
    "hash"      ":" <string> ","           (* SHA-256 hex, 64 chars *)
    "prev_hash" ":" ( <string> | <null> )  (* null for first entry *)
    [ "," "source" ":" <source> ]
    [ "," "urgency" ":" <urgency> ]
    [ "," "details" ":" <object> ]
  "}"

<audit-type>    ::= "intent_executed"
                  | "intent_blocked"
                  | "intent_partial"
                  | "device_registered"
                  | "device_unregistered"
                  | "emergency_intent_blocked_by_policy"
                  | "rate_limit_exceeded"
                  | "policy_applied"
                  | "device_action_executed"
                  | "firmware_reregistration_detected"
```

---

## 8. Hub Status

```ebnf
<hub-status>    ::= "{"
    "hub_id"           ":" <string> ","
    "protocol_version" ":" <string> ","     (* e.g. "0.1" *)
    "api_version"      ":" <string> ","     (* e.g. "1" *)
    "hub_version"      ":" <semver> ","
    "device_count"     ":" <integer> ","
    "audit_entries"    ":" <integer> ","
    "audit_integrity"  ":" <boolean>
    [ "," "transports"    ":" <object> ]
  "}"
```

---

## 9. HTTP endpoints

```ebnf
<endpoint>      ::= <method> " " <path>

<method>        ::= "GET" | "POST" | "DELETE"

<path>          ::= "/v1/status"
                  | "/v1/hub/heartbeat"
                  | "/v1/devices"
                  | "/v1/devices/" <string>
                  | "/v1/intent"
                  | "/v1/intent/" <uuid>
                  | "/v1/intent/explain"
                  | "/v1/events"
                  | "/v1/health/devices"
                  | "/v1/health/devices/" <string>
                  | "/v1/audit"
                  | "/v1/intent-classes"

<ws-path>       ::= "/ws"   (* WebSocket endpoint — real-time event stream *)
                            (* Upgrade: websocket header required *)

<ws-message>    ::= "{"
    "type"       ":" <ws-event-type> ","
    "timestamp"  ":" <timestamp> ","
    "data"       ":" <object>
  "}"

<ws-event-type> ::= "intent_executed"
                  | "intent_blocked"
                  | "device_registered"
                  | "device_unregistered"
                  | "device_event"
                  | "hub_status"

<auth-header>   ::= "Authorization: Bearer " <string>
<version-header> ::= "X-DoSync-Protocol-Version: " <string>
                   | "X-DoSync-API-Version: " <string>
```

---

## 10. Error response

```ebnf
<error-response> ::= "{"
    "detail" ":" <string>
  "}"

<http-error-code> ::= "400"    (* malformed request *)
                    | "401"    (* missing or invalid token *)
                    | "404"    (* device or resource not found *)
                    | "409"    (* duplicate registration *)
                    | "422"    (* unknown intent class or invalid urgency *)
                    | "429"    (* rate limit exceeded *)
                    | "500"    (* internal hub error — never returned for known inputs *)
```

---

*DoSync Protocol v0.1 · Apache 2.0 · github.com/giulianireg-spec/dosync-protocol*
