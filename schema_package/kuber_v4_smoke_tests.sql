/* =====================================================================
   KUBER V4 — SMOKE TESTS (run after 01–05). Any failure raises an exception.
   psql -v ON_ERROR_STOP=1 -f kuber_v4_smoke_tests.sql
   ===================================================================== */
SET search_path = kuber, public;
DO $$
DECLARE v text; r varchar; j json; n int;
BEGIN
  -- 1. canonical states & indicators (same as the HTML)
  SELECT status_indicator INTO v FROM v_payment_current WHERE payment_id = 'PAY-RETURN-000001';
  ASSERT v = 'RJCT · AC03', 'PAY-RETURN-000001 indicator: ' || v;
  SELECT state_label INTO v FROM v_payment_current WHERE payment_id = 'PAY-CB-000001';
  ASSERT v = 'Current State · WAITING_CORRESPONDENT', 'PAY-CB-000001 label: ' || v;
  SELECT confidence::text INTO v FROM fn_kuber_confidence('PAY-CB-000001');
  ASSERT v = '0.94', 'confidence ' || v;

  -- 2. no non-completed payment ever exposes "Completed"
  SELECT count(*) INTO n FROM v_payment_current WHERE status <> 'COMPLETED'
     AND replace(fn_payment_360(payment_id)::text, 'Completed At', '') ~ '(Completed|COMPLETED)';
  ASSERT n = 0, n || ' non-completed payments leak Completed';

  -- 3. the four scenarios end where the spec says
  r := fn_sim_run_to_end('PAY-CB-000001', 'HAPPY');   SELECT status INTO v FROM payment WHERE payment_id = 'PAY-CB-000001'; ASSERT v = 'COMPLETED', 'HAPPY ' || v;   PERFORM fn_sim_reset(r);
  r := fn_sim_run_to_end('PAY-CB-000001', 'TIMEOUT'); SELECT status INTO v FROM payment WHERE payment_id = 'PAY-CB-000001'; ASSERT v = 'FAILED', 'TIMEOUT ' || v;   PERFORM fn_sim_reset(r);
  r := fn_sim_run_to_end('PAY-CB-000001', 'AC03');    SELECT string_agg(state, ',' ORDER BY seq) INTO v FROM v_payment_timeline WHERE payment_id = 'PAY-CB-000001';
  ASSERT v LIKE '%,REJECTED,RETURNED', 'AC03 path ' || v;                                                                                                        PERFORM fn_sim_reset(r);
  r := fn_sim_run_to_end('PAY-SCREEN-000001', 'SCREEN'); SELECT string_agg(state, ',' ORDER BY seq) INTO v FROM v_payment_timeline WHERE payment_id = 'PAY-SCREEN-000001';
  ASSERT v = 'INITIATED,ACCEPTED,SCREENING,INVESTIGATION,SCREENING,SENT,SETTLEMENT_PENDING,COMPLETED', 'SCREEN path ' || v;                                     PERFORM fn_sim_reset(r);
  SELECT status INTO v FROM payment WHERE payment_id = 'PAY-CB-000001'; ASSERT v = 'IN_PROGRESS', 'reset did not restore: ' || v;

  -- 4. failure injection
  r := fn_sim_start('PAY-RTP-000001', 'HAPPY', '10x'); PERFORM fn_sim_step(r);
  j := fn_sim_inject_failure(r);
  ASSERT j->>'PaymentStatus' = 'RJCT' AND j->>'Event' = 'FAILED' AND (j->>'FailureInjected')::boolean, 'injection payload ' || j::text;
  SELECT outcome INTO v FROM sim_run WHERE run_id = r; ASSERT v = 'FAILURE INJECTED', 'outcome ' || v;
  PERFORM fn_sim_reset(r);

  -- 5. guards
  BEGIN UPDATE payment SET status = 'COMPLETED' WHERE payment_id = 'PAY-RETURN-000001'; RAISE EXCEPTION 'guard missing';
  EXCEPTION WHEN check_violation THEN NULL; END;
  BEGIN INSERT INTO payment_event (payment_id, state, description, actor, event_ts) VALUES ('PAY-RETURN-000001', 'COMPLETED', 'x', 'x', now()); RAISE EXCEPTION 'transition guard missing';
  EXCEPTION WHEN check_violation THEN NULL; END;

  -- 6. Kuber routing & human boundary
  SELECT answering_agent_id INTO v FROM fn_kuber_route('Screening & risk?'); ASSERT v = 'risk', 'route ' || v;
  ASSERT (fn_kuber_answer('PAY-CB-000001', 'Explain this payment')->>'text') LIKE 'Payment is progressing through the correspondent chain%', 'summary';
  PERFORM fn_kuber_propose_actions('PAY-TIMEOUT-000001', 'correspondent');
  BEGIN PERFORM fn_decide_action((SELECT min(action_id) FROM ai_proposed_action WHERE payment_id = 'PAY-TIMEOUT-000001'), 'agent.correspondent', 'APPROVE');
        RAISE EXCEPTION 'agent approved its own action';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;

  -- 7. lab
  SELECT option_code INTO v FROM fn_route_rail(25000000, 'DOM', 'INSTANT', 'BH') WHERE recommended; ASSERT v = 'Fedwire', 'router ' || v;
  RAISE NOTICE 'Kuber V4 smoke tests: ALL PASSED';
END $$;
