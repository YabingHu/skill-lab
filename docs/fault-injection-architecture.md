# Independent fault injection engine (initial scaffold)

The diagnostic Agent Skill and the environment fault generator have different
responsibilities. Keep the fault engine independent of `chat.faults`.

## Implemented

- `backend/app/fault_injection/engine.py`: provider contract, registry, lifecycle
  transitions and mock BGP Router-ID conflict provider.
- `backend/tests/test_fault_injection.py`: mock lifecycle and invalid transitions.
- No real device configuration is changed by this provider.

## Integration plan

1. `chat.faults` should reference a provider name and validated test parameters,
   instead of embedding arbitrary device configuration commands.
2. Store fault runs durably, associate `fault_run_id` with `conversation_id`,
   and allow explicit recovery independently of a chat HTTP request.
3. Require per-device locks, authorization, human approval and audit logging before
   registering a production-capable device provider.
4. Fail closed if preparation or verification fails. Persist recovery information
   **before** attempting any device write. Retry recovery through a separate worker,
   and alert operators on `RECOVERY_REQUIRED`.
5. Use the existing `device_connector.execute_commands` behind a device adapter
   only after its CLI confirmation handling and output contracts are tested.

## Testing

From the repository's `backend` directory:

```bash
python -m pytest tests/test_fault_injection.py -q
```

This scaffold intentionally does not register live SSH fault injection and does
not yet modify `chat.py`; it provides a testable separation before integration.
