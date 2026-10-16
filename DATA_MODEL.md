# DATA_MODEL.md: What Is Stored in MongoDB

This file describes the two collections the grader seeds directly into MongoDB. The application must read and write documents using the exact structures described below. API contracts and business rules R1–R10 are defined in `openapi.yaml`.

| Property | Value |
|---|---|
| Database | The database specified by `MONGO_DB` |
| Collections | `employees`, `attendance_logs` |
| Initial indexes | Only MongoDB's default `_id` index; create required application indexes at startup |

## 1. Types at a Glance

| Kind of value | In MongoDB | In the HTTP API |
|---|---|---|
| An instant (punch, creation time, history time) | BSON date in UTC | Integer epoch milliseconds |
| A calendar date (`date`, `joined_on`) | String `YYYY-MM-DD` | Same string |
| A time of day (`shift_start`, `shift_end`) | String `HH:MM`, 24-hour IST | Same string |
| A duration | `work_hours` as a double; minutes as integers | Same numbers |

Calendar dates and times of day are strings because a calendar date is not an instant. Instants are stored as BSON datetimes in UTC and converted to or from epoch milliseconds at the API boundary.

PyMongo returns naive datetimes by default. The application must handle these consistently as UTC.

## 2. Employees Collection

One document per employee.

| Field | Type | Notes |
|---|---|---|
| `_id` | ObjectId | Internal to MongoDB. Never accepted or returned by the API and must not identify employees. |
| `emp_code` | String | Unique, permanent employee identifier assigned by HR, for example `EMP0001`. |
| `name` | String | Employee name. |
| `email` | String | Employee email address. |
| `department` | String | Case-sensitive department name. |
| `shift_start` | String (`HH:MM`) | Shift start time in IST. |
| `shift_end` | String (`HH:MM`) | Shift end time in IST. If it is less than or equal to `shift_start`, the shift is overnight. |
| `joined_on` | String (`YYYY-MM-DD`) | First day of employment. |
| `created_at` | BSON date | Set by the API when creating the employee; never supplied by the client. |

Example document:

```json
{
  "emp_code": "EMP0001",
  "name": "Asha Rao",
  "email": "asha@example.com",
  "department": "Engineering",
  "shift_start": "09:30",
  "shift_end": "18:30",
  "joined_on": "2026-01-05",
  "created_at": {
    "$date": "2026-01-05T04:30:00Z"
  }
}
```

## 3. Attendance Logs Collection

There may be at most one document for each `(emp_code, date)` pair. `emp_code` refers to `employees.emp_code`; it is not a MongoDB ObjectId reference.

| Field | Type | Notes |
|---|---|---|
| `_id` | ObjectId | Internal MongoDB field. Attendance records are addressed by `(emp_code, date)`. |
| `emp_code` | String | Employee identifier. |
| `date` | String (`YYYY-MM-DD`) | Attendance day in IST, determined by rule R1. For an overnight shift, this is the day the shift started. |
| `status` | String | One of the five allowed status values below. |
| `punch_in` | BSON date or null | Set at punch-in; null or missing for `ABSENT` and `LEAVE`. |
| `punch_out` | BSON date or null | Null until punch-out. |
| `work_hours` | Double or null | Computed by the API, never supplied by the client. Null until punch-out. |
| `late_minutes` | Integer | Computed by the API at punch-in. Missing means zero. |
| `overtime_minutes` | Integer | Computed by the API at punch-out. Missing means zero. |
| `half_day` | Boolean | Computed by the API at punch-out. Missing means false. |
| `history` | Array | Audit trail of manual corrections. Missing means an empty array. |

### Status Values

| Status | Meaning | Punch times | Counts as present | Who creates it |
|---|---|---|---|---|
| `PRESENT` | Worked on site | Punch-in required | Yes | Punch-in |
| `WFH` | Worked from home | Punch-in required | Yes | Punch-in |
| `ON_DUTY` | Official duty away from the office | Punch-in required | Yes | Punch-in |
| `ABSENT` | Did not work and was not on approved leave | Both null | No | Seeded back-office data or correction |
| `LEAVE` | On approved leave | Both null | No | Seeded back-office data or correction |

No endpoint creates `ABSENT` or `LEAVE` records during ordinary attendance operations. These may exist in seeded data or be created by a permitted correction.

A weekday without an attendance document is not the same as an `ABSENT` record. It means the day has not been recorded.

### Stored Derived Values Are Authoritative

The API writes `work_hours`, `late_minutes`, `overtime_minutes`, and `half_day` when it performs punch-in, punch-out, or a correction.

Analytics must read these stored values instead of recalculating them from punch timestamps. In the grader's dataset, the stored derived values define the analytics results.

For records written by the application:

| Situation | `work_hours` | `late_minutes` | `overtime_minutes` | `half_day` |
|---|---|---|---|---|
| Presence status without punch-out | Null | Calculated using R2 | Zero | False |
| Presence status with punch-out | Calculated using R4 | Calculated using R2 | Calculated using R3 | Calculated using R5 |
| `ABSENT` or `LEAVE` | Null | Zero | Zero | False |

## 4. History: The Audit Trail

The `history` array contains manual corrections only. Punch-in and punch-out do not append history entries.

Each correction appends exactly one entry. Existing entries must never be edited or removed.

| Field | Type | Notes |
|---|---|---|
| `at` | BSON date | Time the correction was made. |
| `by` | String | Person making the correction; free text because authentication is not required for this assignment. |
| `reason` | String | Reason for the correction. |
| `changes` | Object | One entry per field that actually changed. Each entry contains `from` and `to` values. |

Allowed keys in `changes` are:

- `status`
- `punch_in`
- `punch_out`
- `work_hours`
- `late_minutes`
- `overtime_minutes`
- `half_day`

In MongoDB, `from` and `to` values for `punch_in` and `punch_out` are BSON dates. In the HTTP API, they are epoch milliseconds.

## 5. Sample Documents

The files in `sample_data/` contain the sample documents in MongoDB Extended JSON format. They can be loaded using `python sample_seed.py` or an appropriate `mongoimport --jsonArray` command. MongoDB generates `_id` when it is omitted.

The examples below illustrate document shapes. The grader uses a larger dataset that includes ties, weekend records, employees without attendance logs, employees who join mid-month, and other edge cases.

### 5.1 Normal Full Day

Punched in at 09:28 IST and out at 18:35 IST. The punch-in is within the grace period, and five minutes of overtime is below the 30-minute minimum.

```json
{
  "emp_code": "EMP0001",
  "date": "2026-07-06",
  "status": "PRESENT",
  "punch_in": {
    "$date": "2026-07-06T03:58:00Z"
  },
  "punch_out": {
    "$date": "2026-07-06T13:05:00Z"
  },
  "work_hours": 9.12,
  "late_minutes": 0,
  "overtime_minutes": 0,
  "half_day": false,
  "history": []
}
```

### 5.2 Late Arrival With Overtime

Punched in at 10:05 IST, 35 minutes after a 09:30 shift start, and out at 19:10 IST, 40 minutes after shift end.

```json
{
  "emp_code": "EMP0003",
  "date": "2026-07-06",
  "status": "PRESENT",
  "punch_in": {
    "$date": "2026-07-06T04:35:00Z"
  },
  "punch_out": {
    "$date": "2026-07-06T13:40:00Z"
  },
  "work_hours": 9.08,
  "late_minutes": 35,
  "overtime_minutes": 40,
  "half_day": false,
  "history": []
}
```

### 5.3 Open Record Without Punch-Out

`punch_out` is null, so `work_hours` is null. Other derived fields have their documented defaults.

```json
{
  "emp_code": "EMP0006",
  "date": "2026-07-07",
  "status": "PRESENT",
  "punch_in": {
    "$date": "2026-07-07T04:05:00Z"
  },
  "punch_out": null,
  "work_hours": null,
  "late_minutes": 0,
  "overtime_minutes": 0,
  "half_day": false,
  "history": []
}
```

### 5.4 Half-Day Work From Home

The employee worked 3.5 hours, below the 4.50-hour threshold, so `half_day` is true. This contributes 0.5 of a present day.

```json
{
  "emp_code": "EMP0004",
  "date": "2026-07-07",
  "status": "WFH",
  "punch_in": {
    "$date": "2026-07-07T04:00:00Z"
  },
  "punch_out": {
    "$date": "2026-07-07T07:30:00Z"
  },
  "work_hours": 3.5,
  "late_minutes": 0,
  "overtime_minutes": 0,
  "half_day": true,
  "history": []
}
```

### 5.5 Absent

There are no punch times. This record does not count as present.

```json
{
  "emp_code": "EMP0004",
  "date": "2026-07-08",
  "status": "ABSENT",
  "punch_in": null,
  "punch_out": null,
  "work_hours": null,
  "late_minutes": 0,
  "overtime_minutes": 0,
  "half_day": false,
  "history": []
}
```

### 5.6 Leave

There are no punch times. This record does not count as present.

```json
{
  "emp_code": "EMP0003",
  "date": "2026-07-08",
  "status": "LEAVE",
  "punch_in": null,
  "punch_out": null,
  "work_hours": null,
  "late_minutes": 0,
  "overtime_minutes": 0,
  "half_day": false,
  "history": []
}
```

### 5.7 Overnight Shift (22:00–06:00)

The attendance date is the day the shift started, `2026-07-06`, although punch-out occurred on the following day. The employee punched in at 21:55 IST and out at 06:40 IST the next day, producing 40 minutes of overtime.

```json
{
  "emp_code": "EMP0005",
  "date": "2026-07-06",
  "status": "PRESENT",
  "punch_in": {
    "$date": "2026-07-06T16:25:00Z"
  },
  "punch_out": {
    "$date": "2026-07-07T01:10:00Z"
  },
  "work_hours": 8.75,
  "late_minutes": 0,
  "overtime_minutes": 40,
  "half_day": false,
  "history": []
}
```

### 5.8 Regularized Record With History

HR corrected the punch-in from 10:05 IST to 09:28 IST. The derived fields were recomputed, and one history entry was appended.

```json
{
  "emp_code": "EMP0001",
  "date": "2026-07-07",
  "status": "PRESENT",
  "punch_in": {
    "$date": "2026-07-07T03:58:00Z"
  },
  "punch_out": {
    "$date": "2026-07-07T13:00:00Z"
  },
  "work_hours": 9.03,
  "late_minutes": 0,
  "overtime_minutes": 0,
  "half_day": false,
  "history": [
    {
      "at": {
        "$date": "2026-07-08T05:00:00Z"
      },
      "by": "hr.admin",
      "reason": "biometric glitch",
      "changes": {
        "punch_in": {
          "from": {
            "$date": "2026-07-07T04:35:00Z"
          },
          "to": {
            "$date": "2026-07-07T03:58:00Z"
          }
        },
        "late_minutes": {
          "from": 35,
          "to": 0
        },
        "work_hours": {
          "from": 8.42,
          "to": 9.03
        }
      }
    }
  ]
}
```

### 5.9 Legacy Record Without History or Half-Day Fields

Older records may omit `history` and `half_day`. Treat missing `history` as an empty array and missing `half_day` as false. Missing `late_minutes` and `overtime_minutes` should also be treated as zero.

```json
{
  "emp_code": "EMP0002",
  "date": "2026-07-14",
  "status": "PRESENT",
  "punch_in": {
    "$date": "2026-07-14T04:02:00Z"
  },
  "punch_out": {
    "$date": "2026-07-14T13:01:00Z"
  },
  "work_hours": 8.98,
  "late_minutes": 0,
  "overtime_minutes": 0
}
```

## 6. Implementation Requirements

1. Create the necessary indexes at application startup. At minimum, enforce unique `emp_code` values in `employees` and unique `(emp_code, date)` pairs in `attendance_logs`.
2. Do not use MongoDB `_id` as the employee identifier in the API.
3. Store instants as UTC BSON dates and expose them as epoch milliseconds in the API.
4. Preserve calendar dates as `YYYY-MM-DD` strings.
5. Keep correction history append-only and record only actual field changes.
6. Use stored derived attendance values for analytics.
7. Handle missing legacy fields using the documented defaults.
8. Include employees with no attendance logs when calculating eligible department headcounts.
