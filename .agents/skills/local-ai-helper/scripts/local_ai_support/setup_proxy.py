"""Lazy access to setup helpers without creating an import cycle."""


class SetupSupportProxy:
    def __getattr__(self, name):
        if name in {
            "relative",
            "print_json",
            "safe_cache_slug",
            "cache_file",
            "write_report_cache",
            "as_text_list",
            "normalize_evidence",
            "stable_report",
            "print_report",
            "print_generated_report",
        }:
            from local_ai_support import report_support

            return getattr(report_support, name)
        if name in {
            "EMBEDDING_PROFILE",
        }:
            from local_ai_support import setup_constants

            return getattr(setup_constants, name)
        from local_ai_support import setup_impl

        return getattr(setup_impl, name)


support = SetupSupportProxy()
