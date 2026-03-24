-- Latest 20 text messages: flat sqlite3 columns (UTC via SQL, from, message).
-- For readable multi-line bodies + tags: `poe query-recent-tags` (scripts/format_recent_simple.py).
-- Apple nanoseconds → Unix: (date / 1e9) + 978307200 (matches reader.APPLE_EPOCH_OFFSET).
SELECT
  datetime(CAST(m.date AS REAL) / 1000000000.0 + 978307200, 'unixepoch') AS date,
  CASE
    WHEN m.is_from_me = 1 THEN 'me'
    ELSE COALESCE(h.id, c.chat_identifier, '')
  END AS "from",
  m.text AS message
FROM message m
JOIN chat_message_join cmj ON m.rowid = cmj.message_id
JOIN chat c ON cmj.chat_id = c.rowid
LEFT JOIN handle h ON m.handle_id = h.rowid
WHERE m.text IS NOT NULL
  AND m.text != ''
  AND m.associated_message_type = 0
ORDER BY m.date DESC
LIMIT 20;
