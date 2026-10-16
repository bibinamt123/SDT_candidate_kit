# DECISIONS.md

### 1. Indexes

I use indexes to make employee lookups and attendance queries faster. The employee code index supports finding an employee by `emp_code`. The attendance date and employee index, `attendance_date_employee`, supports filtering and sorting attendance records. I would also consider a unique compound index on employee code and attendance date to prevent duplicate daily attendance records, but only if the data model and business rules guarantee one record per employee per date.

### 2. Punch-in race

If two identical punch-in requests arrive simultaneously, both may check for an existing record before either inserts one. Application-level checking alone cannot reliably prevent this race. A unique database index on the appropriate attendance key is needed to allow only one insert. The request that loses the race should return a controlled conflict response rather than an internal server error.

### 3. Ties

The leaderboard uses competition ranking, so employees with equal late-minute totals receive the same rank. Employees tied at the cutoff are included when their rank is within the requested limit. This preserves ties instead of arbitrarily excluding an employee.

### 4. Headcount

The department summary should start from eligible employee records and look up attendance records separately. This ensures employees who have joined by the end of the selected month are counted even when they have no attendance logs.

### 5. Scaling

For 100 times more data, I would review query execution statistics, optimize indexes against real query patterns, and test aggregation performance on a dataset similar to production.

