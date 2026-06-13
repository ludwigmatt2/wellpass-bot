-- Migration: track when a watch last changed status (diagnose missed bookings)
-- Run this in the Supabase SQL editor BEFORE deploying the matching app version.
-- Safe to run more than once (idempotent).

-- 1. Add the column. DEFAULT now() means new inserts get it automatically.
ALTER TABLE watches
  ADD COLUMN IF NOT EXISTS status_changed_at TIMESTAMPTZ DEFAULT now();

-- 2. Backfill existing rows. We have no real cancel/expire timestamp for past
--    rows, so created_at is the best available proxy (the 5 already-CANCELLED
--    watches get created_at; this is approximate for them, exact going forward).
UPDATE watches
  SET status_changed_at = created_at
  WHERE status_changed_at IS NULL;

-- 3. Auto-stamp status_changed_at on every real status transition.
--    Server-side trigger => the app never has to set it, so there is no
--    deploy-ordering hazard and no code path can forget to update it.
--    When status = 'CANCELLED', this column IS the cancelled_at.
CREATE OR REPLACE FUNCTION set_status_changed_at()
RETURNS TRIGGER AS $$
BEGIN
  IF NEW.status IS DISTINCT FROM OLD.status THEN
    NEW.status_changed_at = now();
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_watches_status_changed ON watches;
CREATE TRIGGER trg_watches_status_changed
  BEFORE UPDATE ON watches
  FOR EACH ROW
  EXECUTE FUNCTION set_status_changed_at();
