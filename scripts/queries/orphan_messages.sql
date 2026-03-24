SELECT COUNT(*) AS orphan_messages
FROM message m
WHERE NOT EXISTS (
  SELECT 1 FROM chat_message_join cmj WHERE cmj.message_id = m.rowid
);
