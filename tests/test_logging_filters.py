import logging

from django.test import SimpleTestCase

from webcaf.logging_filters import ExcludeEmailQueriesFilter


class ExcludeEmailQueriesFilterTest(SimpleTestCase):
    def test_sql_without_email_addresses_is_still_logged(self):
        record = logging.makeLogRecord({"msg": "SELECT %s", "args": ("organisation",)})
        self.assertTrue(ExcludeEmailQueriesFilter().filter(record))

    def test_withholds_queries_with_email_addresses_in_literals_or_parameters(self):
        for email in ("person@example.gov.uk", '"quoted local part"@example.gov.uk', "invalid@address"):
            for msg, args in ((f"INSERT '{email}'", ()), ("INSERT params=%s", (email,))):
                with self.subTest(email=email, msg=msg):
                    record = logging.makeLogRecord({"msg": msg, "args": args})
                    self.assertFalse(ExcludeEmailQueriesFilter().filter(record))
