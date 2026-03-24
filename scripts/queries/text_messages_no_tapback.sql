SELECT COUNT(*) AS text_messages_no_tapback
FROM message
WHERE text IS NOT NULL
  AND text != ''
  AND associated_message_type = 0;
