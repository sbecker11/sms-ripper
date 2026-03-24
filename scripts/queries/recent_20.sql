SELECT
  m.rowid,
  c.chat_identifier,
  h.id AS sender,
  m.is_from_me,
  m.date,
  substr(m.text, 1, 80) AS text_preview
FROM message m
JOIN chat_message_join cmj ON m.rowid = cmj.message_id
JOIN chat c ON cmj.chat_id = c.rowid
LEFT JOIN handle h ON m.handle_id = h.rowid
WHERE m.text IS NOT NULL
  AND m.text != ''
  AND m.associated_message_type = 0
ORDER BY m.date DESC
LIMIT 20;
