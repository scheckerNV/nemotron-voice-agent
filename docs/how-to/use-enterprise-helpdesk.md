# Use the Enterprise Help-Desk Demo

Select `enterprise_helpdesk` in the Generic Assistant to speak with Nova, the fictional Northstar IT help-desk assistant. Its `lookup_ticket` tool reads synthetic records from the repository. It has no connection to a real IT system and cannot create, update, approve, escalate, or close tickets.

The preset changes the persona and enables one tool. It reuses your existing speech recognition, large language model (LLM), and speech synthesis services. It requires no additional server, model download, API key, or Compose recipe.

## Select the Persona

Start the [Generic Assistant](../../src/examples/generic/README.md) with the recipe that matches your host. For an existing Thor deployment, keep the `generic-assistant/single-gpu` recipe and current service settings.

Open `https://<host>:7860` in your browser. While disconnected, choose `enterprise_helpdesk` from the **PROMPT** selector, then connect and allow microphone access. Nova should introduce the Northstar demo help desk and ask for a ticket number. In the **Tools** tab, confirm that `lookup_ticket` is enabled for this preset.

Disconnect and reconnect after changing presets so the pipeline registers the new tool selection. The original default prompt remains unchanged.

## Try a Ticket Conversation

Ask “What is the status of IT one zero zero one?” Nova should execute a lookup and answer in one or two short spoken sentences. Then ask “What is the next step?” That same-ticket follow-up can use the latest actual tool result already in the conversation.

New tickets, current-status questions, latest-update questions, and explicit refresh requests require a lookup. Other same-ticket details can reuse the latest returned record; missing details require another lookup. Previous assistant wording or general model knowledge is not a substitute for a tool result.

The checked-in fixtures contain the following records:

| Ticket | Subject | Status |
| --- | --- | --- |
| `IT-1001` | VPN disconnects during video calls | Investigating |
| `IT-1002` | Laptop cannot connect to the office display | Resolved |
| `IT-1003` | Access to the analytics workspace | Waiting for approval |

Start a new session for the missing-number check: “What is my ticket status?” should prompt for a number. `IT-9999` should return no matching demo record. “Close IT-1001” should explain the read-only limit. Then ask “Refresh the status of IT-1001 from the records”: a new lookup should still return Investigating.

The lookup accepts four digits with an optional `IT` prefix, ignoring spaces, hyphens, and letter case. The prompt instructs the LLM to convert spoken digits into a ticket ID. Speech recognition and tool selection still need end-to-end validation on your host.

The function itself cannot write records. Choosing the tool and answering from its result still depend on the model; prompting does not guarantee every response follows these rules. Verify both the executed call and the spoken facts.

## Follow the Tool Call

The tool runs inside the Generic Assistant process. These files define the behavior:

| File | Role |
| --- | --- |
| [prompts.yaml](../../src/examples/generic/prompts.yaml) | Nova persona and `tools_available: [lookup_ticket]` |
| [tools.yaml](../../src/examples/generic/tools.yaml) | Function schema with the required `ticket_id` argument |
| [tool_handlers.py](../../src/examples/generic/tool_handlers.py) | Registered handler and result callback |
| [ticket_lookup.py](../../src/examples/generic/ticket_lookup.py) | Local read-only lookup and error handling |
| [ticket_fixtures.json](../../src/examples/generic/ticket_fixtures.json) | Fictional training data, licensed Apache 2.0 |

From the repository root, inspect a fixture result without starting GPU services:

```bash
PYTHONPATH=src python3 -c 'from examples.generic.ticket_lookup import lookup_ticket; print(lookup_ticket("IT-1001"))'
```

The result includes `source: synthetic_training_data`, `found: True`, and the ticket fields. Invalid IDs, missing records, and unreadable fixtures return explicit errors instead of fabricated ticket details.

On an existing Thor deployment, inspect the application log after a spoken lookup:

```bash
docker compose --profile generic-assistant/single-gpu logs --tail 100 generic-assistant-single-gpu
```

Look for `Registered tool handler: lookup_ticket` and `lookup_ticket completed: found`. Keep any additional Compose files used by your deployment in these commands. The **Tools** tab shows available schemas; it is not a tool-execution trace. For distributed traces and per-stage timings, refer to [OpenTelemetry tracing](enable-opentelemetry-tracing.md).

For reproducible API checks, set `temperature: 0` in the initial gateway session update to reduce sampling randomness:

```json
{"type":"session.update","session":{"type":"realtime","prompt":{"id":"enterprise_helpdesk"},"temperature":0}}
```

The browser has no dedicated temperature control. Test its actual behavior separately; a successful API check does not prove browser or microphone behavior.

## Customize the Persona

In your own checkout, edit only the `enterprise_helpdesk` content in `src/examples/generic/prompts.yaml`. For example, replace the response-order instruction so status answers name the assigned team first. Keep the preset key and `tools_available` list, then restart the existing application, refresh the browser, and reconnect. Refer to [Configure Prompts](configure-prompts.md) for catalog editing.

A new custom prompt created in the browser does not inherit the built-in preset's tool list. Preserve the `enterprise_helpdesk` key for this exercise. Keep all added fixture data fictional.

## Use Reachy Mini

With the [Reachy audio client](use-reachy-audio.md) prepared on the Thor, select the preset explicitly:

```bash
uv run --no-project --with websockets==15.0.1 python scripts/reachy_audio_client.py --insecure --prompt-key enterprise_helpdesk --allow-interruptions
```

`--prompt-key` sends the catalog selection through the [Realtime Gateway](use-realtime-gateway.md). It preserves the selected persona instead of supplying the client's default Reachy instructions. `--insecure` applies only to the local self-signed demonstration; omit it for a trusted certificate. Optional head turning remains a separate client flag.

Disconnect the browser session or press **Ctrl+C** in the Reachy client when finished. Select the original preset to return to the previous persona. Restore any local prompt edits before sharing the checkout.
