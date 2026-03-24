SELECT COUNT(*) AS empty_or_null_text FROM message WHERE text IS NULL OR text = '';
