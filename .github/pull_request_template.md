## What & why

## Safety impact
<!-- Could this change delay, suppress or mis-route an alert? Change who can see evidence? -->
- [ ] No effect on alerting / escalation / evidence access
- [ ] Affects them — explained above and covered by tests

## Verification
- [ ] Backend tests pass (`cd backend && pytest`)
- [ ] Edge host tests pass (`make -C edge/esp32/tests/host`)
- [ ] Docs updated where behaviour or configuration changed
- [ ] Nothing labelled complete that is actually PROTOTYPE / MOCK / PENDING INTEGRATION
