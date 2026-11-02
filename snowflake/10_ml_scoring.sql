-- Scores every robot-minute with the active model, in plain SQL (no Python runtime needed).
-- Requires a row in ML.MODEL_REGISTRY (created by `python -m analytics.train_model`).
USE ROLE FLEETPULSE_DEV;
USE DATABASE FLEETPULSE;
USE WAREHOUSE FLEETPULSE_WH;

CREATE OR REPLACE VIEW ML.RISK_SCORES AS
WITH m AS (
  SELECT * FROM ML.MODEL_REGISTRY WHERE is_active
  QUALIFY ROW_NUMBER() OVER (ORDER BY trained_at DESC) = 1
),
scored AS (
  SELECT f.run_id, f.robot_id, f.window_start + INTERVAL '1 minute' AS window_end,
         f.avg_temp, f.current_rel_5m, f.temp_rise_5m, f.tripped_so_far AS already_tripped, m.model_id, m.threshold,
         1 / (1 + EXP(-(
             m.intercept
           + m.current_rel_5m_coef       * (f.current_rel_5m       - m.current_rel_5m_mean)       / m.current_rel_5m_std
           + m.current_rel_delta_5m_coef * (f.current_rel_delta_5m - m.current_rel_delta_5m_mean) / m.current_rel_delta_5m_std
           + m.temp_rise_5m_coef         * (f.temp_rise_5m         - m.temp_rise_5m_mean)         / m.temp_rise_5m_std
           + m.temp_rise_delta_5m_coef   * (f.temp_rise_delta_5m   - m.temp_rise_delta_5m_mean)   / m.temp_rise_delta_5m_std
           + m.fault_count_10m_coef      * (f.fault_count_10m      - m.fault_count_10m_mean)      / m.fault_count_10m_std
         ))) AS risk_15m
  FROM ML.FEATURES_1MIN f CROSS JOIN m
  WHERE f.current_rel_delta_5m IS NOT NULL AND f.temp_rise_delta_5m IS NOT NULL AND f.n_records >= 30
    AND f.minutes_since_start >= 15
    AND f.charge_share_15m = 0
)
SELECT *, risk_15m >= threshold AND NOT already_tripped AS alert FROM scored;

-- Robots currently flagged, most recent window per robot
SELECT run_id, robot_id, window_end, ROUND(risk_15m, 3) AS risk_15m, ROUND(avg_temp, 1) AS avg_temp
FROM ML.RISK_SCORES
QUALIFY ROW_NUMBER() OVER (PARTITION BY run_id, robot_id ORDER BY window_end DESC) = 1 AND alert
ORDER BY risk_15m DESC;
