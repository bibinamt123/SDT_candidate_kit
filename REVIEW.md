# REVIEW.md

## Defects Found in the Starter `app/main.py`

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | Timestamp validation helper | Timestamp inputs may accept invalid types or values, such as booleans, floats, strings, or timestamps expressed in seconds instead of milliseconds. | Send invalid timestamp values to the punch-in or punch-out endpoint and check that they are rejected with HTTP 422. | Added strict validation for integer epoch-millisecond timestamps and rejected invalid types and out-of-range values. |
| 2 | Timestamp conversion helper | Timestamp handling may not consistently convert epoch milliseconds to UTC datetimes with seconds truncated. | Submit timestamps containing fractional seconds and inspect the stored attendance record. | Standardized timestamp conversion to UTC BSON datetimes and truncated seconds as required. |
| 3 | Attendance punch-in endpoint | Punch-in validation may not correctly prevent duplicate or otherwise invalid punch-in operations. | Submit repeated punch-in requests for the same employee and attendance date. | Added the required validation and duplicate-record handling according to the attendance rules. |
| 4 | Attendance punch-out endpoint | Punch-out may not correctly validate an existing punch-in or calculate derived attendance fields. | Attempt punch-out without a valid punch-in, or test work hours, late minutes, overtime, and half-day calculations. | Added the required validation and corrected the derived-field calculations according to the specification. |
| 5 | Attendance correction endpoint | Manual corrections may not preserve a complete audit history or may record unchanged fields. | PATCH an attendance record and inspect its history; verify that only changed fields appear in the correction entry. | Updated correction handling to append an audit entry containing the timestamp, user, reason, and changed fields. |
| 6 | Monthly employee analytics endpoint | Monthly metrics may have incorrect response fields, attendance percentage calculations, or working-day calculations. | Request `/analytics/employees/EMP0007/monthly?month=2026-10` and compare the response with the required schema and sample attendance data. | Reworked the endpoint to calculate the required metrics using MongoDB aggregation, including weekday presence, leave days, late minutes, overtime, and attendance percentage. |
| 7 | Department summary endpoint | The summary may omit the required month filter, return incorrect field names, calculate metrics in Python, or count employees who had not joined by the end of the selected month. | Request the summary for a specific month and compare the response fields, headcount, presence totals, and average work hours with the specification. | Needs to be completed and verified: use a month-filtered MongoDB aggregation, count eligible employees, and return the required fields. |
| 8 | Department trend endpoint | A trend may omit dates without attendance records, or calculate attendance rates and moving averages incorrectly. | Request a date range containing weekends or dates with no attendance records and verify that every date has a row. | Added date densification and aggregation-based trend calculations. The empty-range case still needs verification. |
| 9 | Admin explain endpoint | Explain results may describe a simplified query rather than the actual query or aggregation used by an endpoint. | Call each supported explain endpoint and compare its execution plan with the corresponding endpoint's actual database operation. | Added explain commands using MongoDB execution statistics. Exact pipeline equivalence and index usage still need verification for all supported endpoints. |
| 10 | Attendance list pagination | Pagination or filtering may apply the wrong page offset, allow an excessive page size, or calculate totals before applying filters. | Test page 1 and page 2, invalid page values, maximum page size, and filtered result totals. | Verify and enforce the specified pagination defaults, limits, sorting, and filtered total count. |
| 11 | MongoDB index initialization | Required indexes may not be created at startup or index creation may not be safely repeatable. | Start the application multiple times and inspect the indexes on the relevant collections. | Ensure required indexes are created idempotently during application startup. |

## Items Checked and Not Considered Defects

| # | Where | What was checked | Why it was not considered a defect |
|---|---|---|---|
| 1 | Attendance list query plan | Index usage | The tested attendance-list query used an `IXSCAN` with the `attendance_date_employee` index, rather than a collection scan. This confirms index use for that tested query only. |
| 2 | Late-comers leaderboard | Ranking with tied totals | Competition ranking is correct when equal totals share a rank and the following rank skips accordingly. |
| 3 | Department trend | Weekend handling | Weekend attendance records may still contribute to presence and late counts. The working-day flag controls whether an attendance rate is calculated. |
| 4 | Attendance status handling | Presence statuses | `PRESENT`, `WFH`, and `ON_DUTY` count as presence, while `ABSENT` and `LEAVE` do not count as present days. |
| 5 | Monthly analytics | Employees who joined during the month | Working days should be counted from the employee's joining date through the end of the selected month, rather than automatically counting the entire month. |

## Notes

This review should describe defects in the original starter implementation and the changes made to address them. Items marked as needing verification must be tested before the final submission. Update the table to include only defects supported by code inspection or reproducible tests, and document the actual fix for each one.