SELECT
  CASE WHEN is_from_me = 1 THEN 'from_me' ELSE 'from_them' END AS direction,
  COUNT(*) AS n
FROM message
GROUP BY is_from_me;
