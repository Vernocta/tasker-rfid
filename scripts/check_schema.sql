-- Confirms every table from SPEC.md section 3 exists.
-- Every row should say "ok". A "MISSING" row means the migration did not fully apply.

SELECT expected.table_name,
       CASE WHEN t.table_name IS NULL THEN 'MISSING' ELSE 'ok' END AS status
FROM (VALUES
    ('skus'),('customers'),('containers'),('container_contents'),
    ('reads_raw'),('observations'),('dispatch_sessions'),('movements'),
    ('anomalies'),('cycle_counts'),('cycle_count_items')
) AS expected(table_name)
LEFT JOIN information_schema.tables t
       ON t.table_name = expected.table_name
      AND t.table_schema = 'public'
ORDER BY expected.table_name;