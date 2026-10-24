"""Validated query filters shared by the analytics endpoints. Only enum/regex-checked values reach SQL, and always
as bound parameters."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from fastapi import Depends, HTTPException, Query, Request
from psycopg import Connection

from services.api.app.db import one, ro_conn

Tier = Literal["free", "pro", "team", "enterprise", "no_account"]
Segment = Literal["smb", "mid_market", "enterprise", "no_account"]
Plan = Literal["free", "pro"]


@dataclass(frozen=True)
class Filters:
    start: date
    end: date
    as_of: date
    tier: str | None = None
    segment: str | None = None
    plan: str | None = None
    feature: str | None = None

    def user_clause(self, alias: str = "u") -> tuple[str, list]:
        """SQL fragment (leading AND) + params restricting `alias` (the users table) by tier/segment/plan."""
        sql, params = "", []
        for col, val in (("account_tier", self.tier), ("account_segment", self.segment), ("current_plan", self.plan)):
            if val is not None:
                sql += f" AND {alias}.{col} = %s"
                params.append(val)
        return sql, params

    def echo(self) -> dict:
        return {"start": self.start, "end": self.end, "tier": self.tier, "segment": self.segment,
                "plan": self.plan, "feature": self.feature}


def get_filters(
    request: Request,
    start: date | None = Query(None, description="Inclusive UTC start date (default: first date with data)"),
    end: date | None = Query(None, description="Inclusive UTC end date (default: latest date with data)"),
    tier: Tier | None = Query(None, description="Account tier"),
    segment: Segment | None = Query(None, description="Account segment"),
    plan: Plan | None = Query(None, description="CURRENT plan of the user (not the plan at event time)"),
    feature: str | None = Query(None, pattern=r"^feat_[a-z_]{1,40}$", description="Feature id"),
    conn: Connection = Depends(ro_conn),
) -> Filters:
    bounds = one(conn, "SELECT min(date) AS lo, max(date) AS hi FROM serving.mart_kpi_daily")
    as_of = one(conn, "SELECT as_of_date FROM serving.publish_meta WHERE id = 1")
    if not bounds or bounds["lo"] is None or not as_of:
        raise HTTPException(503, "No data has been published yet.")
    s, e = start or bounds["lo"], end or bounds["hi"]
    if s > e:
        raise HTTPException(422, "start must be on or before end")
    if (e - s) > timedelta(days=request.app.state.settings.max_range_days):
        raise HTTPException(422, f"date range may not exceed {request.app.state.settings.max_range_days} days")
    if feature is not None and not one(conn, "SELECT 1 FROM serving.features WHERE feature_id = %s", [feature]):
        raise HTTPException(422, f"unknown feature '{feature}'")
    return Filters(s, e, as_of["as_of_date"], tier, segment, plan, feature)
