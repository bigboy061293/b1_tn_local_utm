"""Dữ liệu 5 trạm RIS (I-06): bản ghi đã chuẩn hoá, trạng thái trạm, tỷ lệ đối chiếu Remote ID theo chuyến."""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from b1.db import Base

S = {"schema": "surveillance"}


class StationRecord(Base):
    """Một bản ghi từ trạm: adsb | rid | weather | spectrum | status. Ba nhãn thời gian: nguồn, trạm (PTP), ghi vào CSDL."""
    __tablename__ = "record"
    __table_args__ = (Index("record_stream_time", "stream", "t_station"), S)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    station_id: Mapped[str] = mapped_column(String(10), index=True)
    stream: Mapped[str] = mapped_column(String(10))
    key: Mapped[str | None] = mapped_column(String(64), index=True)  # ICAO 24 bit hoặc UAS ID
    t_source: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    t_station: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    t_receive: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    t_ingest: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=text("clock_timestamp()"))
    backfill: Mapped[bool] = mapped_column(Boolean, default=False)
    data: Mapped[dict] = mapped_column(JSONB)


class StationState(Base):
    __tablename__ = "station_state"
    __table_args__ = S
    station_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    status: Mapped[dict | None] = mapped_column(JSONB)
    status_t_station: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    weather: Mapped[dict | None] = mapped_column(JSONB)
    weather_t_station: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_receive: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class RidReconciliation(Base):
    """Đối chiếu Broadcast Remote ID với telemetry của cùng chuyến (Đặc tả Mục 5.1, AT-05)."""
    __tablename__ = "rid_reconciliation"
    __table_args__ = S
    flight_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    uas_id: Mapped[str] = mapped_column(String(64))
    total: Mapped[int] = mapped_column(Integer, default=0)
    matched: Mapped[int] = mapped_column(Integer, default=0)
    mismatched: Mapped[int] = mapped_column(Integer, default=0)
    no_telemetry: Mapped[int] = mapped_column(Integer, default=0)
    max_distance_m: Mapped[float] = mapped_column(default=0.0)
    by_station: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=text("clock_timestamp()"))
