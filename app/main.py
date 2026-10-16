import os
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "attendance_db")

client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
db = client[MONGO_DB]

UTC = timezone.utc
IST = ZoneInfo("Asia/Kolkata")

app = FastAPI(
    title="Employee Attendance & Analytics API",
    version="2.0.0",
)


# ---------- Common helpers ----------

def round_half_up(value: float, places: int = 2) -> float:
    quantum = Decimal("1").scaleb(-places)
    return float(
        Decimal(str(value)).quantize(
            quantum,
            rounding=ROUND_HALF_UP,
        )
    )


def parse_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422,
            detail="Date must use YYYY-MM-DD format",
        )

    if parsed.isoformat() != value:
        raise HTTPException(
            status_code=422,
            detail="Date must use YYYY-MM-DD format",
        )

    return parsed


def validate_shift(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:[01]\d|2[0-3]):[0-5]\d",
        value,
    ):
        raise ValueError("Time must use HH:MM format")

    return value


def parse_epoch_ms(value: int) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int):
        raise HTTPException(
            status_code=422,
            detail="Timestamp must be an integer in milliseconds",
        )

    if not 100000000000 <= value <= 4102444800000:
        raise HTTPException(
            status_code=422,
            detail="Timestamp is outside the allowed range",
        )

    # Convert milliseconds to UTC and truncate to whole seconds.
    return datetime.fromtimestamp(value // 1000, tz=UTC)


def utc_now_seconds() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC).replace(microsecond=0)

    return value.astimezone(UTC).replace(microsecond=0)


def shift_datetime(
    day: date,
    shift_time: str,
    tz=IST,
) -> datetime:
    hour, minute = map(int, shift_time.split(":"))

    return datetime.combine(
        day,
        time(hour, minute),
        tzinfo=tz,
    )


def attendance_date_for_punch(
    punched_at: datetime,
    employee: dict,
) -> date:
    local = to_utc(punched_at).astimezone(IST)
    local_day = local.date()

    start = employee["shift_start"]
    end = employee["shift_end"]

    # For an overnight shift, early-morning punches can
    # belong to the previous day's shift.
    if end <= start and local.strftime("%H:%M") < end:
        return local_day - timedelta(days=1)

    return local_day


def shift_start_for_day(
    day: date,
    employee: dict,
) -> datetime:
    return shift_datetime(day, employee["shift_start"])


def shift_end_for_day(
    day: date,
    employee: dict,
) -> datetime:
    start = employee["shift_start"]
    end = employee["shift_end"]

    end_day = day + timedelta(days=1) if end <= start else day

    return shift_datetime(end_day, end)


def compute_late_minutes(
    punch_in: datetime,
    shift_start: str,
    day: date,
) -> int:
    scheduled_start = shift_datetime(day, shift_start)

    difference = int(
        (
            to_utc(punch_in)
            - scheduled_start.astimezone(UTC)
        ).total_seconds()
        // 60
    )

    # Late only when more than 10 minutes after shift start.
    # Minutes are calculated from the actual shift start.
    return difference if difference > 10 else 0


def compute_work_hours(
    punch_in: datetime,
    punch_out: datetime,
) -> float:
    seconds = (
        to_utc(punch_out) - to_utc(punch_in)
    ).total_seconds()

    return round_half_up(seconds / 3600, 2)


def compute_overtime(
    punch_out: datetime,
    shift_end: str,
    day: date,
    employee: dict,
) -> int:
    scheduled_end = shift_end_for_day(
        day,
        employee,
    ).astimezone(UTC)

    minutes = int(
        (
            to_utc(punch_out) - scheduled_end
        ).total_seconds()
        // 60
    )

    # Overtime is counted only when it reaches 30 minutes.
    return minutes if minutes >= 30 else 0


def calculate_attendance_fields(
    employee: dict,
    day: date,
    punch_in: Optional[datetime],
    punch_out: Optional[datetime],
) -> dict:
    if punch_in is None or punch_out is None:
        return {
            "work_hours": None,
            "late_minutes": (
                compute_late_minutes(
                    punch_in,
                    employee["shift_start"],
                    day,
                )
                if punch_in is not None
                else 0
            ),
            "overtime_minutes": 0,
            "half_day": False,
        }

    work_hours = compute_work_hours(
        punch_in,
        punch_out,
    )

    return {
        "work_hours": work_hours,
        "late_minutes": compute_late_minutes(
            punch_in,
            employee["shift_start"],
            day,
        ),
        "overtime_minutes": compute_overtime(
            punch_out,
            employee["shift_end"],
            day,
            employee,
        ),
        "half_day": work_hours < 4.50,
    }


def serialize_document(document: dict) -> dict:
    document = dict(document)
    document.pop("_id", None)

    # Attendance-only defaults. Do not add these fields to employees.
    if "date" in document and "emp_code" in document:
        document.setdefault("history", [])
        document.setdefault("half_day", False)

    def convert_datetimes(value):
        if isinstance(value, datetime):
            value = to_utc(value)
            return int(value.timestamp() * 1000)

        if isinstance(value, dict):
            return {
                key: convert_datetimes(item)
                for key, item in value.items()
            }

        if isinstance(value, list):
            return [
                convert_datetimes(item)
                for item in value
            ]

        return value

    return convert_datetimes(document)


# ---------- Database indexes ----------

@app.on_event("startup")
def create_indexes():
    db.employees.create_index(
        [("emp_code", ASCENDING)],
        unique=True,
        name="employee_code_unique",
    )

    db.employees.create_index(
        [
            ("department", ASCENDING),
            ("emp_code", ASCENDING),
        ],
        name="employee_department_code",
    )

    db.employees.create_index(
        [("joined_on", ASCENDING)],
        name="employee_joined_on",
    )

    db.attendance_logs.create_index(
        [
            ("emp_code", ASCENDING),
            ("date", ASCENDING),
        ],
        unique=True,
        name="attendance_employee_date_unique",
    )

    db.attendance_logs.create_index(
        [
            ("date", DESCENDING),
            ("emp_code", ASCENDING),
        ],
        name="attendance_date_employee",
    )

    db.attendance_logs.create_index(
        [
            ("status", ASCENDING),
            ("date", DESCENDING),
        ],
        name="attendance_status_date",
    )


# ---------- Employee request model ----------

class EmployeeIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str

    @field_validator("emp_code")
    @classmethod
    def validate_emp_code(cls, value):
        if not re.fullmatch(r"EMP\d{4,6}", value):
            raise ValueError(
                "emp_code must match EMP followed by 4 to 6 digits"
            )

        return value

    @field_validator("name", "email", "department")
    @classmethod
    def validate_required_text(cls, value):
        if not value:
            raise ValueError("This field cannot be empty")

        return value

    @field_validator("shift_start", "shift_end")
    @classmethod
    def validate_shift_time(cls, value):
        return validate_shift(value)

    @field_validator("joined_on")
    @classmethod
    def validate_joined_on(cls, value):
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            raise ValueError("joined_on must use YYYY-MM-DD format")

        if parsed.isoformat() != value:
            raise ValueError("joined_on must use YYYY-MM-DD format")

        return value


# ---------- Health ----------

@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except PyMongoError:
        raise HTTPException(
            status_code=503,
            detail="MongoDB unavailable",
        )


# ---------- Employees ----------

@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if body.shift_start == body.shift_end:
        raise HTTPException(
            status_code=422,
            detail="shift_start and shift_end must differ",
        )

    document = body.model_dump()
    document["created_at"] = utc_now_seconds()

    try:
        db.employees.insert_one(document)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="emp_code already exists",
        )
    except PyMongoError:
        raise HTTPException(
            status_code=503,
            detail="Database operation failed",
        )

    return serialize_document(document)


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = {}

    if department is not None:
        query["department"] = department

    total = db.employees.count_documents(query)
    skip = (page - 1) * page_size

    items = list(
        db.employees.find(query, {"_id": 0})
        .sort("emp_code", ASCENDING)
        .skip(skip)
        .limit(page_size)
    )

    items = [
        serialize_document(item)
        for item in items
    ]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ---------- Attendance request models ----------

class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = None
    status: str = "PRESENT"

    @field_validator("punched_at")
    @classmethod
    def validate_timestamp(cls, value):
        if value is not None:
            parse_epoch_ms(value)

        return value


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = None

    @field_validator("punched_at")
    @classmethod
    def validate_timestamp(cls, value):
        if value is not None:
            parse_epoch_ms(value)

        return value


class RegularizeIn(BaseModel):
    status: Optional[str] = None
    punch_in: Optional[StrictInt] = None
    punch_out: Optional[StrictInt] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)

    @field_validator("reason", "regularized_by")
    @classmethod
    def validate_text(cls, value):
        if not value.strip():
            raise ValueError("This field cannot be empty")

        return value.strip()

    @field_validator("status")
    @classmethod
    def validate_status(cls, value):
        if value is not None and value not in {
            "PRESENT",
            "ABSENT",
            "LEAVE",
            "WFH",
            "ON_DUTY",
        }:
            raise ValueError("Invalid attendance status")

        return value

    @field_validator("punch_in", "punch_out")
    @classmethod
    def validate_timestamp(cls, value):
        if value is not None:
            parse_epoch_ms(value)

        return value


# ---------- Punch in ----------

@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    employee = db.employees.find_one(
        {"emp_code": body.emp_code}
    )

    if employee is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    if body.status not in {"PRESENT", "WFH", "ON_DUTY"}:
        raise HTTPException(
            status_code=422,
            detail="Invalid attendance status",
        )

    punched_at = (
        parse_epoch_ms(body.punched_at)
        if body.punched_at is not None
        else utc_now_seconds()
    )

    attendance_day = attendance_date_for_punch(
        punched_at,
        employee,
    )

    document = {
        "emp_code": body.emp_code,
        "date": attendance_day.isoformat(),
        "status": body.status,
        "punch_in": punched_at,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(
            punched_at,
            employee["shift_start"],
            attendance_day,
        ),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(document)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="Attendance already exists for this date",
        )
    except PyMongoError:
        raise HTTPException(
            status_code=503,
            detail="Database operation failed",
        )

    return serialize_document(document)


# ---------- Punch out ----------

@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    employee = db.employees.find_one(
        {"emp_code": body.emp_code}
    )

    if employee is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    punched_at = (
        parse_epoch_ms(body.punched_at)
        if body.punched_at is not None
        else utc_now_seconds()
    )

    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {
                "$ne": None,
                "$lte": punched_at,
            },
        },
        sort=[("punch_in", DESCENDING)],
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="No matching punch-in record found",
        )

    if record.get("punch_out") is not None:
        raise HTTPException(
            status_code=409,
            detail="Already punched out",
        )

    punch_in_time = to_utc(record["punch_in"])

    if punched_at <= punch_in_time:
        raise HTTPException(
            status_code=422,
            detail="Punch-out must be after punch-in",
        )

    if punched_at > punch_in_time + timedelta(hours=24):
        raise HTTPException(
            status_code=422,
            detail="Punch-out cannot be more than 24 hours later",
        )

    attendance_day = parse_date(record["date"])

    fields = calculate_attendance_fields(
        employee,
        attendance_day,
        punch_in_time,
        punched_at,
    )

    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": punched_at,
                **fields,
            }
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance record was already updated",
        )

    updated = db.attendance_logs.find_one(
        {"_id": record["_id"]}
    )

    return serialize_document(updated)


# ---------- List attendance ----------

@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = {}

    if emp_code is not None:
        query["emp_code"] = emp_code

    if status is not None:
        query["status"] = status

    if date_from is not None:
        parse_date(date_from)
        query.setdefault("date", {})["$gte"] = date_from

    if date_to is not None:
        parse_date(date_to)
        query.setdefault("date", {})["$lte"] = date_to

    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status_code=422,
            detail="date_from cannot be after date_to",
        )

    total = db.attendance_logs.count_documents(query)
    skip = (page - 1) * page_size

    records = list(
        db.attendance_logs.find(query, {"_id": 0})
        .sort([
            ("date", DESCENDING),
            ("emp_code", ASCENDING),
        ])
        .skip(skip)
        .limit(page_size)
    )

    records = [
        serialize_document(record)
        for record in records
    ]

    return {
        "items": records,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ---------- Attendance regularization ----------

@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: str,
    body: RegularizeIn,
):
    attendance_day = parse_date(date)

    employee = db.employees.find_one(
        {"emp_code": emp_code}
    )

    if employee is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    record = db.attendance_logs.find_one({
        "emp_code": emp_code,
        "date": attendance_day.isoformat(),
    })

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="Attendance record not found",
        )

    old_status = record["status"]

    old_in = (
        to_utc(record["punch_in"])
        if record.get("punch_in") is not None
        else None
    )

    old_out = (
        to_utc(record["punch_out"])
        if record.get("punch_out") is not None
        else None
    )

    new_status = (
        body.status
        if body.status is not None
        else old_status
    )

    if new_status in {"ABSENT", "LEAVE"}:
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(
                status_code=422,
                detail="ABSENT or LEAVE cannot include punch times",
            )

        new_in = None
        new_out = None

    else:
        new_in = (
            parse_epoch_ms(body.punch_in)
            if body.punch_in is not None
            else old_in
        )

        new_out = (
            parse_epoch_ms(body.punch_out)
            if body.punch_out is not None
            else old_out
        )

        if new_in is None:
            raise HTTPException(
                status_code=422,
                detail="Presence statuses require a punch_in",
            )

        if (
            attendance_date_for_punch(new_in, employee)
            != attendance_day
        ):
            raise HTTPException(
                status_code=422,
                detail="punch_in must belong to the attendance date",
            )

        if new_out is not None:
            if new_out <= new_in:
                raise HTTPException(
                    status_code=422,
                    detail="punch_out must be after punch_in",
                )

            if new_out > new_in + timedelta(hours=24):
                raise HTTPException(
                    status_code=422,
                    detail="punch_out cannot be more than 24 hours later",
                )

    derived = calculate_attendance_fields(
        employee,
        attendance_day,
        new_in,
        new_out,
    )

    old_values = {
        "status": old_status,
        "punch_in": old_in,
        "punch_out": old_out,
        "work_hours": record.get("work_hours"),
        "late_minutes": record.get("late_minutes", 0),
        "overtime_minutes": record.get("overtime_minutes", 0),
        "half_day": record.get("half_day", False),
    }

    new_values = {
        "status": new_status,
        "punch_in": new_in,
        "punch_out": new_out,
        **derived,
    }

    changes = {}

    for field, new_value in new_values.items():
        old_value = old_values[field]

        if old_value != new_value:
            if field in {"punch_in", "punch_out"}:
                old_history_value = (
                    int(old_value.timestamp() * 1000)
                    if old_value is not None
                    else None
                )

                new_history_value = (
                    int(new_value.timestamp() * 1000)
                    if new_value is not None
                    else None
                )
            else:
                old_history_value = old_value
                new_history_value = new_value

            changes[field] = {
                "from": old_history_value,
                "to": new_history_value,
            }

    if not changes:
        raise HTTPException(
            status_code=422,
            detail="The request does not change the record",
        )

    history_entry = {
        "at": utc_now_seconds(),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }

    update_values = {
        "status": new_status,
        "punch_in": new_in,
        "punch_out": new_out,
        **derived,
    }

    # Check the original values to prevent overwriting a concurrent
    # update. Support legacy records where history is missing.
    update_filter = {
        "_id": record["_id"],
        "status": old_status,
        "punch_in": record.get("punch_in"),
        "punch_out": record.get("punch_out"),
    }

    if "history" in record:
        update_filter["history"] = record["history"]
    else:
        update_filter["history"] = {"$exists": False}

    result = db.attendance_logs.update_one(
        update_filter,
        {
            "$set": update_values,
            "$push": {"history": history_entry},
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance record changed concurrently; retry the request",
        )

    updated = db.attendance_logs.find_one(
        {"_id": record["_id"]}
    )

    return serialize_document(updated)


# ---------- Department summary analytics ----------

@app.get("/analytics/departments/summary")
def department_summary():
    pipeline = [
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$sum": 1},
                "employees": {"$push": "$emp_code"},
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "localField": "employees",
                "foreignField": "emp_code",
                "as": "attendance",
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": 1,
                "attendance": 1,
            }
        },
        {"$sort": {"department": 1}},
    ]

    departments = list(db.employees.aggregate(pipeline))
    results = []

    for department in departments:
        # Ignore attendance records dated before an employee joined.
        employee_codes = set(department.get("employees", []))
        attendance = department.get("attendance", [])

        # Retrieve employee joining dates for correct period filtering.
        employee_docs = list(
            db.employees.find(
                {"emp_code": {"$in": list(employee_codes)}},
                {"_id": 0, "emp_code": 1, "joined_on": 1},
            )
        )
        joined_dates = {
            item["emp_code"]: parse_date(item["joined_on"])
            for item in employee_docs
        }

        attendance = [
            record
            for record in attendance
            if record.get("emp_code") in joined_dates
            and parse_date(record["date"])
            >= joined_dates[record["emp_code"]]
        ]

        present_records = [
            record
            for record in attendance
            if record.get("status") in {"PRESENT", "WFH", "ON_DUTY"}
            and record.get("work_hours") is not None
        ]

        average_work_hours = (
            round_half_up(
                sum(record["work_hours"] for record in present_records)
                / len(present_records),
                2,
            )
            if present_records
            else 0.0
        )

        results.append(
            {
                "department": department["department"],
                "headcount": department["headcount"],
                "average_work_hours": average_work_hours,
                "late_count": sum(
                    1 for record in attendance
                    if record.get("late_minutes", 0) > 0
                ),
                "late_minutes": sum(
                    record.get("late_minutes", 0)
                    for record in attendance
                ),
                "leave_count": sum(
                    1 for record in attendance
                    if record.get("status") == "LEAVE"
                ),
                "on_duty_count": sum(
                    1 for record in attendance
                    if record.get("status") == "ON_DUTY"
                ),
            }
        )

    return {"items": results}


# ---------- Monthly employee analytics ----------

@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly_analytics(
    emp_code: str,
    month: str = Query(..., description="Month in YYYY-MM format"),
):
    # Validate the month.
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(
            status_code=422,
            detail="month must use YYYY-MM format",
        )

    year, month_number = map(int, month.split("-"))

    try:
        month_start = date(year, month_number, 1)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Invalid month",
        )

    if month_number == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month_number + 1, 1)

    month_end = next_month - timedelta(days=1)

    employee = db.employees.find_one({"emp_code": emp_code})

    if employee is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    joined_on = parse_date(employee["joined_on"])

    # Count weekdays from the employee's joining date
    # through the end of the selected month.
    effective_start = max(month_start, joined_on)

    working_days = 0
    current_day = effective_start

    while current_day <= month_end:
        if current_day.weekday() < 5:
            working_days += 1

        current_day += timedelta(days=1)

    # Read stored derived values; do not recalculate attendance
    # metrics from punch timestamps.
    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {
                    "$gte": month_start.isoformat(),
                    "$lte": month_end.isoformat(),
                },
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    {
                                        "$gte": [
                                            {"$dayOfWeek": {
                                                "$dateFromString": {
                                                    "dateString": "$date"
                                                }
                                            }},
                                            2,
                                        ]
                                    },
                                    {
                                        "$lte": [
                                            {"$dayOfWeek": {
                                                "$dateFromString": {
                                                    "dateString": "$date"
                                                }
                                            }},
                                            6,
                                        ]
                                    },
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$half_day", True]},
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "late_minutes": {
                    "$sum": {"$ifNull": ["$late_minutes", 0]}
                },
                "overtime_minutes": {
                    "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                },
                "leave_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$status", "LEAVE"]},
                            1,
                            0,
                        ]
                    }
                },
                "on_duty_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$status", "ON_DUTY"]},
                            1,
                            0,
                        ]
                    }
                },
                "work_hours_total": {
                    "$sum": {"$ifNull": ["$work_hours", 0]}
                },
                "work_hours_count": {
                    "$sum": {
                        "$cond": [
                            {"$ne": ["$work_hours", None]},
                            1,
                            0,
                        ]
                    }
                },
            }
        },
    ]

    results = list(db.attendance_logs.aggregate(pipeline))

    summary = results[0] if results else {}

    present_days = summary.get("present_days", 0)
    work_hours_count = summary.get("work_hours_count", 0)
    work_hours_total = summary.get("work_hours_total", 0)

    average_work_hours = (
        round_half_up(work_hours_total / work_hours_count, 2)
        if work_hours_count
        else 0.0
    )

    attendance_rate = (
        round_half_up(present_days / working_days, 4)
        if working_days
        else 0.0
    )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "attendance_rate": attendance_rate,
        "late_minutes": summary.get("late_minutes", 0),
        "overtime_minutes": summary.get("overtime_minutes", 0),
        "leave_count": summary.get("leave_count", 0),
        "on_duty_count": summary.get("on_duty_count", 0),
        "average_work_hours": average_work_hours,
    }



# ---------- Late-comers leaderboard ----------

@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(..., description="Month in YYYY-MM format"),
    limit: int = Query(10, ge=1, le=50),
    department: Optional[str] = None,
):
    # Validate month format.
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(
            status_code=422,
            detail="month must use YYYY-MM format",
        )

    year, month_number = map(int, month.split("-"))

    try:
        month_start = date(year, month_number, 1)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid month")

    if month_number == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month_number + 1, 1)

    month_end = next_month - timedelta(days=1)

    pipeline = [
        {
            "$match": {
                "date": {
                    "$gte": month_start.isoformat(),
                    "$lte": month_end.isoformat(),
                }
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$late_minutes", 0]}
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$gt": [
                                    {"$ifNull": ["$late_minutes", 0]},
                                    0,
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {
            "$match": {
                "total_late_minutes": {"$gt": 0}
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
    ]

    # Apply department filtering before calculating ranks.
    if department is not None:
        pipeline.append(
            {"$match": {"employee.department": department}}
        )

    pipeline.extend(
        [
            {
                "$setWindowFields": {
                    "sortBy": {"total_late_minutes": -1},
                    "output": {
                        "rank": {"$rank": {}}
                    },
                }
            },
            # Keep everyone whose rank is within the requested limit.
            {"$match": {"rank": {"$lte": limit}}},
            # Resolve ties alphabetically without changing their ranks.
            {
                "$sort": {
                    "total_late_minutes": -1,
                    "_id": 1,
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "rank": 1,
                    "emp_code": "$_id",
                    "name": "$employee.name",
                    "department": "$employee.department",
                    "total_late_minutes": 1,
                    "late_count": 1,
                }
            },
        ]
    )

    items = list(db.attendance_logs.aggregate(pipeline))

    return {
        "month": month,
        "items": items,
    }



# ---------- Department attendance trend ----------

@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: date = Query(..., alias="from"),
    to_date: date = Query(..., alias="to"),
):
    from datetime import datetime, time, timezone

    if to_date < from_date:
        raise HTTPException(
            status_code=422,
            detail="'to' must be on or after 'from'",
        )

    if (to_date - from_date).days + 1 > 92:
        raise HTTPException(
            status_code=422,
            detail="Date range cannot exceed 92 days",
        )

    if db.employees.count_documents({"department": department}) == 0:
        raise HTTPException(
            status_code=404,
            detail="Department not found",
        )

    start_dt = datetime.combine(
        from_date, time.min, tzinfo=timezone.utc
    )
    end_dt = datetime.combine(
        to_date + timedelta(days=1), time.min, tzinfo=timezone.utc
    )

    pipeline = [
        {
            "$match": {
                "date": {
                    "$gte": from_date.isoformat(),
                    "$lte": to_date.isoformat(),
                }
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
        {"$match": {"employee.department": department}},
        {
            "$addFields": {
                "_trend_date": {
                    "$dateFromString": {
                        "dateString": "$date",
                        "format": "%Y-%m-%d",
                        "timezone": "UTC",
                    }
                }
            }
        },
        {
            "$group": {
                "_id": "$_trend_date",
                "present_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$in": [
                                    "$status",
                                    ["PRESENT", "WFH", "ON_DUTY"],
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$half_day", True]},
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$gt": [
                                    {"$ifNull": ["$late_minutes", 0]},
                                    0,
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {
            "$densify": {
                "field": "_id",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [start_dt, end_dt],
                },
            }
        },
        {
            "$set": {
                "present_count": {
                    "$ifNull": ["$present_count", 0]
                },
                "late_count": {
                    "$ifNull": ["$late_count", 0]
                },
                "date_string": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$_id",
                        "timezone": "UTC",
                    }
                },
                "weekday": {
                    "$dayOfWeek": "$_id"
                },
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "let": {
                    "day_string": "$date_string",
                    "dept": {"$literal": department},
                },
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$department", "$$dept"]},
                                    {"$lte": ["$joined_on", "$$day_string"]},
                                ]
                            }
                        }
                    },
                    {"$project": {"_id": 0, "emp_code": 1}},
                ],
                "as": "joined_employees",
            }
        },
        {
            "$set": {
                "headcount": {"$size": "$joined_employees"},
                "is_working_day": {
                    "$not": [
                        {"$in": ["$weekday", [1, 7]]}
                    ]
                },
            }
        },
        {
            "$set": {
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {"$gt": ["$headcount", 0]},
                            ]
                        },
                        {
                            "$divide": [
                                {
                                    "$floor": {
                                        "$add": [
                                            {
                                                "$multiply": [
                                                    {
                                                        "$divide": [
                                                            "$present_count",
                                                            "$headcount",
                                                        ]
                                                    },
                                                    10000,
                                                ]
                                            },
                                            0.5,
                                        ]
                                    }
                                },
                                10000,
                            ]
                        },
                        None,
                    ]
                }
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"_id": 1},
                "output": {
                    "moving_avg_raw": {
                        "$avg": "$attendance_rate",
                        "window": {"documents": [-6, 0]},
                    }
                },
            }
        },
        {
            "$set": {
                "moving_avg_7d": {
                    "$cond": [
                        {"$eq": ["$moving_avg_raw", None]},
                        None,
                        {
                            "$divide": [
                                {
                                    "$floor": {
                                        "$add": [
                                            {
                                                "$multiply": [
                                                    "$moving_avg_raw",
                                                    10000,
                                                ]
                                            },
                                            0.5,
                                        ]
                                    }
                                },
                                10000,
                            ]
                        },
                    ]
                }
            }
        },
        {
            "$sort": {"_id": 1}
        },
        {
            "$project": {
                "_id": 0,
                "date": "$date_string",
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_7d": 1,
            }
        },
    ]

    items = list(db.attendance_logs.aggregate(pipeline))

    return {
        "department": department,
        "items": items,
    }



# ---------- MongoDB explain endpoint ----------

@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = Query(10, ge=1, le=50),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    from_date: Optional[date] = Query(None, alias="from"),
    to_date: Optional[date] = Query(None, alias="to"),
):
    allowed = {
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend",
    }

    if endpoint not in allowed:
        raise HTTPException(
            status_code=422,
            detail="Unsupported endpoint",
        )

    if endpoint in {
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
    }:
        if month is None or not re.fullmatch(
            r"\d{4}-(0[1-9]|1[0-2])", month
        ):
            raise HTTPException(
                status_code=422,
                detail="A valid month in YYYY-MM format is required",
            )

    if endpoint == "employee_monthly":
        if not emp_code:
            raise HTTPException(
                status_code=422,
                detail="emp_code is required",
            )

        year, month_num = map(int, month.split("-"))
        first_day = date(year, month_num, 1)
        if month_num == 12:
            next_month = date(year + 1, 1, 1)
        else:
            next_month = date(year, month_num + 1, 1)

        query = {
            "emp_code": emp_code,
            "date": {
                "$gte": first_day.isoformat(),
                "$lt": next_month.isoformat(),
            },
        }
        
        explain = db.attendance_logs.database.command(
            "explain",
            {
                "find": "attendance_logs",
                "filter": query,
            },
            verbosity="executionStats",
        )

        collection = "attendance_logs"

    elif endpoint == "attendance_list":
        query = {}
        if emp_code:
            query["emp_code"] = emp_code
        if status:
            query["status"] = status
        if date_from or date_to:
            query["date"] = {}
            if date_from:
                query["date"]["$gte"] = date_from.isoformat()
            if date_to:
                query["date"]["$lte"] = date_to.isoformat()

        
        
        explain = db.attendance_logs.database.command(
            "explain",
            {
                "find": "attendance_logs",
                "filter": query,
                "sort": {"date": -1, "emp_code": 1},
                "skip": (page - 1) * page_size,
                "limit": page_size,
            },
            verbosity="executionStats",
        )


        collection = "attendance_logs"

    elif endpoint == "department_summary":
        pipeline = [
            {
                "$match": {
                    "date": {
                        "$regex": f"^{re.escape(month)}",
                    }
                }
            },
            {"$sort": {"date": 1}},
        ]
        explain = db.attendance_logs.database.command(
            "explain",
            {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {},
            },
            verbosity="executionStats",
        )
        collection = "attendance_logs"

    elif endpoint == "late_leaderboard":
        year, month_num = map(int, month.split("-"))
        first_day = date(year, month_num, 1)
        if month_num == 12:
            next_month = date(year + 1, 1, 1)
        else:
            next_month = date(year, month_num + 1, 1)

        pipeline = [
            {
                "$match": {
                    "date": {
                        "$gte": first_day.isoformat(),
                        "$lt": next_month.isoformat(),
                    }
                }
            },
            {"$sort": {"emp_code": 1, "date": 1}},
        ]
        explain = db.attendance_logs.database.command(
            "explain",
            {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {},
            },
            verbosity="executionStats",
        )
        collection = "attendance_logs"

    else:  # department_trend
        if not department or not from_date or not to_date:
            raise HTTPException(
                status_code=422,
                detail="department, from, and to are required",
            )

        if to_date < from_date or (to_date - from_date).days + 1 > 92:
            raise HTTPException(
                status_code=422,
                detail="Invalid trend date range",
            )

        pipeline = [
            {
                "$match": {
                    "date": {
                        "$gte": from_date.isoformat(),
                        "$lte": to_date.isoformat(),
                    }
                }
            },
            {"$sort": {"date": 1, "emp_code": 1}},
        ]
        explain = db.attendance_logs.database.command(
            "explain",
            {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {},
            },
            verbosity="executionStats",
        )
        collection = "attendance_logs"

    return {
        "endpoint": endpoint,
        "collection": collection,
        "explain": explain,
    }
