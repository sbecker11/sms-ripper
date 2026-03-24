SELECT
  c.chat_identifier,
  COUNT(*) AS message_count
FROM chat_message_join cmj
JOIN chat c ON c.rowid = cmj.chat_id
JOIN message m ON m.rowid = cmj.message_id
WHERE m.text IS NOT NULL AND m.text != '' AND m.associated_message_type = 0
GROUP BY c.rowid
ORDER BY message_count DESC
LIMIT 25;
