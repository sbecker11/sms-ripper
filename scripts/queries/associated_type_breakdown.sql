SELECT associated_message_type, COUNT(*) AS n
FROM message
GROUP BY associated_message_type
ORDER BY n DESC
LIMIT 20;
