"""Lower scalar engineering operations into the existing Spark SQL intermediate."""

from cadeo.models.specification import SpecificationValidationError


class RegistrySqlService:
    def __init__(self, sql):
        self.sql = sql

    def _literal(self, value):
        from sqlglot import exp
        return exp.Literal.string(value).sql(dialect="spark") if isinstance(value, str) else self.sql.literal(value)

    def render(self, name, args):
        literal = self._literal
        expression = self.sql.expression
        source = args.get("_source") or (expression(args["expression"]) if "expression" in args else None)
        text = f"CAST({source} AS STRING)"

        def case(condition, yes, no="NULL"):
            return f"(CASE WHEN {condition} THEN {yes} ELSE {no} END)"

        def clean(value, options):
            if options.get("trim"):
                value = f"TRIM({value})"
            if options.get("collapse_whitespace"):
                whitespace = literal("\\s+")
                value = f"REGEXP_REPLACE({value}, {whitespace}, ' ')"
            mode = options.get("case", "preserve")
            if mode == "capitalize":
                value = f"CONCAT(UPPER(SUBSTRING({value}, 1, 1)), LOWER(SUBSTRING({value}, 2)))"
            elif mode != "preserve":
                value = f"{ {'upper': 'UPPER', 'lower': 'LOWER', 'title': 'INITCAP'}[mode]}({value})"
            if options.get("blank_as_null"):
                value = f"NULLIF({value}, '')"
            return value

        def mapping(value, values, fallback="NULL", sensitive=True, data_type=None, exact=False):
            key = f"CAST({value} AS STRING)"
            if not sensitive:
                key = f"LOWER({key})"
            branches = []
            for before, after in values.items():
                match = literal(before) if exact else literal(str(before) if sensitive else str(before).lower())
                condition = f"{value} IS NULL" if before is None else f"{value if exact else key} = {match}"
                result = convert(literal(after), data_type) if data_type else literal(after)
                branches.append(f"WHEN {condition} THEN {result}")
            if data_type:
                fallback = convert(fallback, data_type)
            return "(CASE " + " ".join(branches) + f" ELSE {fallback} END)" if branches else fallback

        def convert(value, data_type):
            if data_type is None:
                return value
            options = data_type if isinstance(data_type, dict) else {"name": data_type}
            target = options.get("name").lower()
            target = {"str": "string", "text": "string", "int": "integer", "number": "numeric",
                      "decimal": "numeric", "datetime": "timestamp", "bool": "boolean"}.get(target, target)
            types = {"string": "STRING", "integer": "BIGINT", "numeric": "DECIMAL",
                     "date": "DATE", "timestamp": "TIMESTAMP", "boolean": "BOOLEAN"}
            if target not in types:
                raise SpecificationValidationError(f"{name}: unsupported data_type {target!r}")
            if set(options) - {"name", "precision", "scale", "true_values", "false_values"}:
                raise SpecificationValidationError(f"{name}: unknown conversion options")
            if target == "boolean":
                normalized = f"LOWER(TRIM(CAST({value} AS STRING)))"
                truth = options.get("true_values", ["true", "t", "yes", "y", "1"])
                false = options.get("false_values", ["false", "f", "no", "n", "0"])
                return case(f"{normalized} IN ({', '.join(literal(str(item).lower()) for item in truth)})", "TRUE",
                            case(f"{normalized} IN ({', '.join(literal(str(item).lower()) for item in false)})", "FALSE"))
            sql_type = types[target]
            if target == "string":
                return f"CAST({value} AS STRING)"
            if target != "string":
                value = f"NULLIF(TRIM(CAST({value} AS STRING)), '')"
            if target == "numeric":
                precision, scale = options.get("precision", 38), options.get("scale", 6)
                if type(precision) is not int or type(scale) is not int or not 1 <= precision <= 38 or not 0 <= scale <= precision:
                    raise SpecificationValidationError(f"{name}: invalid numeric precision/scale")
                sql_type += f"({precision}, {scale})"
            return f"TRY_CAST({value} AS {sql_type})"

        def valid(predicate, values=None):
            values = values or [source]
            nulls = " OR ".join(f"{value} IS NULL" for value in values)
            return case(nulls, literal(args.get("allow_null", False)), f"({predicate})")

        def window(function):
            partition = args.get("partition_by")
            return f"{function} OVER (" + ("PARTITION BY " + ", ".join(expression(item) for item in partition) if partition else "") + ")"

        simple = {"string_lower": "LOWER", "string_casefold": "LOWER", "string_upper": "UPPER",
                  "string_strip": "TRIM", "string_remove_whitespace": "TRIM", "string_left_strip": "LTRIM",
                  "string_right_strip": "RTRIM", "string_title": "INITCAP"}
        if name in simple:
            return f"{simple[name]}({text})"
        if name in {"clean_string", "clean_email", "format_string", "null_if_blank", "clean_code"}:
            options = args
            if name == "clean_email":
                options = {"trim": True, "collapse_whitespace": True, "case": "lower", "blank_as_null": True}
            elif name == "null_if_blank":
                return f"NULLIF(TRIM({text}), '')"
            if name == "clean_code":
                text = clean(text, {"trim": True, "collapse_whitespace": True, "blank_as_null": args["blank_as_null"]})
                for key in ("prefix", "suffix"):
                    token = args.get("remove_" + key)
                    if token:
                        text = self.render("string_remove_" + key, {"expression": {"literal": ""}, key: token, "_source": text})
                options = dict(args, trim=True, collapse_whitespace=True)
            result = clean(text, options)
            if name == "clean_code":
                result = mapping(result, args["corrections"], result)
            if args.get("null_value") is not None:
                result = f"COALESCE({result}, {literal(args['null_value'])})"
            return result
        if name in {"clean_integer", "clean_numeric", "clean_date", "clean_timestamp", "clean_boolean", "try_cast", "convert_value"}:
            target = args["data_type"] if name in {"try_cast", "convert_value"} else {"name": name.removeprefix("clean_")}
            if isinstance(target, dict):
                target = dict(target, **{key: args[key] for key in ("precision", "scale", "true_values", "false_values") if key in args})
            return convert(source, target)
        if name in {"mapping", "map_value", "key_value_map", "correct_errors"} or name.startswith("map_"):
            values = args.get("mapping", args.get("correction_map", {}))
            target = args.get("data_type")
            if name in {"map_string", "map_integer", "map_numeric", "map_date", "map_timestamp", "map_boolean"}:
                target = {"name": name[4:], **{key: args[key] for key in ("precision", "scale", "true_values", "false_values") if key in args}}
            result = mapping(source, values, source if args.get("preserve_unmapped") or name == "correct_errors" else literal(args.get("default")), args.get("case_sensitive", True), target, name == "correct_errors")
            formatting = args.get("format", args.get("formatting"))
            if formatting is not None:
                result = self._format(result, target, formatting)
            return clean(result, args) if name == "map_string" else result
        if name == "fill_missing":
            return f"COALESCE({source}, {literal(args['value'])})"
        if name in {"standardize_code", "standardize_country", "standardize_currency"}:
            result = mapping(f"UPPER(TRIM({text}))", args["aliases"], f"UPPER(TRIM({text}))")
            length = {"standardize_country": 2, "standardize_currency": 3}.get(name, args.get("length"))
            return case(f"LENGTH({result}) = {length}", result) if length is not None else result
        if name in {"normalize_whitespace", "string_remove_all_whitespace", "string_remove_punctuation", "regex_replace_all", "clean_phone"}:
            pattern = args.get("pattern", r"\s+" if name != "string_remove_punctuation" else r"[^\w\s]")
            replacement = args.get("replacement", " " if name == "normalize_whitespace" else "")
            if name == "clean_phone":
                pattern = "[^0-9+]" if args["keep_plus"] else "[^0-9]"
                return f"NULLIF(REGEXP_REPLACE(NULLIF(TRIM({text}), ''), {literal(pattern)}, ''), '')"
            return f"REGEXP_REPLACE({text}, {literal(pattern)}, {literal(replacement)})"
        if name in {"regex_match", "is_email"}:
            pattern = args.get("pattern", r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
            result = f"REGEXP_LIKE({text}, {literal(pattern)})"
            return valid(result) if name == "is_email" else result
        if name in {"string_regex_extract", "string_regex_extract_all"}:
            function = "REGEXP_EXTRACT_ALL" if name.endswith("_all") else "REGEXP_EXTRACT"
            return f"{function}({text}, {literal(args['pattern'])}, {args['group']})"
        if name in {"string_replace_many", "string_regex_replace_many", "string_translate_chars"}:
            for before, after in args.get("replacements", args.get("translations", {})).items():
                function = "REGEXP_REPLACE" if name == "string_regex_replace_many" else "REPLACE"
                text = f"{function}({text}, {literal(before)}, {literal(after)})"
            return text
        if name in {"string_remove_prefix", "string_remove_suffix"}:
            text = args.get("_source", text)
            token = args.get("prefix", args.get("suffix"))
            if not token:
                return text
            size = len(token)
            if name.endswith("prefix"):
                return case(f"SUBSTRING({text}, 1, {size}) = {literal(token)}", f"SUBSTRING({text}, {size + 1})", text)
            return case(f"RIGHT({text}, {size}) = {literal(token)}", f"SUBSTRING({text}, 1, LENGTH({text}) - {size})", text)
        if name == "string_capitalize":
            return f"CONCAT(UPPER(SUBSTRING({text}, 1, 1)), LOWER(SUBSTRING({text}, 2)))"
        if name in {"string_slice", "string_slice_replace"}:
            start = args["start"] or 0
            stop = args["stop"]
            portion = f"SUBSTRING({text}, {start + 1}" + (f", {max(0, stop - start)})" if stop is not None else ")")
            if name == "string_slice":
                return portion
            stop = max(start, stop) if stop is not None else None
            return f"CONCAT(SUBSTRING({text}, 1, {start}), {literal(args['replacement'])}, " + (f"SUBSTRING({text}, {stop + 1}))" if stop is not None else "'')")
        if name == "string_split_part":
            return f"SPLIT_PART({text}, {literal(args['delimiter'])}, {args['part']})"
        if name in {"string_pad", "string_center", "string_ljust", "string_rjust", "string_zfill"}:
            width = args["width"]
            fill = literal(args.get("fillchar", "0" if name == "string_zfill" else " "))
            side = {"string_center": "both", "string_ljust": "right", "string_rjust": "left", "string_zfill": "left"}.get(name, args.get("side"))
            if side == "both":
                target = f"CAST(LENGTH({text}) + FLOOR(({width} - LENGTH({text})) / 2) AS INT)"
                padded = f"RPAD(LPAD({text}, {target}, {fill}), {width}, {fill})"
            else:
                padded = f"{'LPAD' if side == 'left' else 'RPAD'}({text}, {width}, {fill})"
            if name == "string_zfill":
                padded = case(f"LEFT({text}, 1) IN ('+', '-')", f"CONCAT(LEFT({text}, 1), LPAD(SUBSTRING({text}, 2), {max(0, width - 1)}, '0'))", padded)
            return case(f"LENGTH({text}) >= {width}", text, padded)
        if name == "unicode_normalize":
            raise SpecificationValidationError("unicode_normalize requires an exporter with an explicit Unicode normalization capability; use its dbt macro mapping")
        if name in {"safe_add", "safe_subtract", "surrogate_key"}:
            fields = [expression(item) for item in args["fields"]]
            if name == "surrogate_key":
                return "MD5(CONCAT(" + ", '-', ".join(f"COALESCE(CAST({item} AS STRING), '_dbt_utils_surrogate_key_null_')" for item in fields) + "))"
            return "(" + (" + " if name == "safe_add" else " - ").join(f"COALESCE({item}, 0)" for item in fields) + ")"
        if name == "safe_divide":
            return f"({expression(args['numerator'])} / NULLIF({expression(args['denominator'])}, 0))"
        if name == "log_transform":
            return f"LN({source} + {literal(args['offset'])})"
        if name in {"clip", "winsorize", "replace_outside_bounds", "within_bounds", "is_between", "is_positive"}:
            lower = args.get("lower", args.get("lower_bound", args.get("min_value")))
            upper = args.get("upper", args.get("upper_bound", args.get("max_value")))
            if name == "is_positive":
                return valid(f"{source} > 0")
            if name in {"clip", "winsorize"}:
                return case(f"{source} < {literal(lower)}", literal(lower), case(f"{source} > {literal(upper)}", literal(upper), source))
            inclusive = args.get("inclusive", True)
            bounds = ([f"{source} {'>=' if inclusive else '>'} {literal(lower)}"] if lower is not None else [])
            bounds += ([f"{source} {'<=' if inclusive else '<'} {literal(upper)}"] if upper is not None else [])
            predicate = " AND ".join(bounds) or "TRUE"
            if name == "replace_outside_bounds":
                return case(f"{source} IS NULL", "NULL", case(predicate, source, literal(args["replacement"])))
            return valid(predicate)
        if name == "is_in_list":
            values = args["values"]
            if not args["case_sensitive"]:
                source, values = f"LOWER({text})", [str(item).lower() for item in values]
            return valid(f"{source} IN ({', '.join(literal(item) for item in values)})" if values else "FALSE")
        if name == "label_encode":
            return mapping(source, {item: index for index, item in enumerate(args["categories"])}, literal(args["unknown"]), exact=True)
        if name == "bin_numeric":
            edges = args["edges"]
            branches = []
            for index, (lower, upper) in enumerate(zip(edges, edges[1:])):
                left = ">=" if not args["right"] or (index == 0 and args["include_lowest"]) else ">"
                right = "<=" if args["right"] else "<"
                label = args["labels"][index] if args["labels"] is not None else index
                branches.append(f"WHEN {source} {left} {literal(lower)} AND {source} {right} {literal(upper)} THEN {literal(label)}")
            return "(CASE " + " ".join(branches) + " ELSE NULL END)"
        if name in {"min_max_scale", "standard_score", "is_zscore_anomaly", "percentile"}:
            if name == "percentile":
                return window(f"PERCENTILE({source}, {literal(args['percentile'])})")
            if name == "min_max_scale":
                minimum, maximum = window(f"MIN({source})"), window(f"MAX({source})")
                result = f"(CAST({source} - {minimum} AS DOUBLE) / NULLIF({maximum} - {minimum}, 0))"
                return case(f"{source} IS NULL", "NULL", case(f"{maximum} = {minimum}", literal(args["zero_range_value"]), result))
            deviation = window(f"STDDEV_SAMP({source})")
            result = f"(({source} - {window(f'AVG({source})')}) / NULLIF({deviation}, 0))"
            return f"COALESCE(ABS({result}) > {literal(args['threshold'])}, FALSE)" if name == "is_zscore_anomaly" else case(f"{source} IS NULL", "NULL", case(f"{deviation} IS NULL OR {deviation} = 0", literal(args["zero_variance_value"]), result))
        if name == "is_iqr_anomaly":
            first, third = expression(args["q1_expression"]), expression(args["q3_expression"])
            span = f"({literal(args['threshold'])} * ({third} - {first}))"
            return f"COALESCE(({source} < {first} - {span} OR {source} > {third} + {span}), FALSE)"
        if name == "is_modified_zscore_anomaly":
            return f"COALESCE((ABS(0.6745 * ({source} - {expression(args['median_expression'])}) / NULLIF({expression(args['mad_expression'])}, 0)) > {literal(args['threshold'])}), FALSE)"
        if name in {"rule_compare", "rule_date_order"}:
            left = expression(args["left"] if name == "rule_compare" else args["start_date"])
            right = expression(args["right"] if name == "rule_compare" else args["end_date"])
            operator = args["operator"] if name == "rule_compare" else "<="
            return valid(f"{left} {operator} {right}", [left, right])
        if name == "rule_required_when":
            return f"(NOT COALESCE({expression(args['when_expression'])}, FALSE) OR {expression(args['required_expression'])} IS NOT NULL)"
        if name == "rule_one_of":
            return "(" + " + ".join(case(f"{expression(item)} IS NOT NULL", "1", "0") for item in args["expressions"]) + " = 1)"
        if name == "rule_sum_equals":
            total = expression(args["total"])
            components = [expression(item) for item in args["components"]]
            return valid(f"ABS({total} - ({' + '.join(components)})) <= {literal(args['tolerance'])}", [total, *components])
        if name == "mask_redact":
            return case(f"{source} IS NULL", "NULL", literal(args["replacement"])) if args["preserve_null"] else literal(args["replacement"])
        if name == "mask_hash":
            value = f"COALESCE({text}, '_dbt_utils_surrogate_key_null_')"
            if args["salt_expression"] is not None:
                value = f"CONCAT({value}, '-', COALESCE(CAST({expression(args['salt_expression'])} AS STRING), '_dbt_utils_surrogate_key_null_'))"
            return f"MD5({value})"
        if name in {"format_date", "format_timestamp", "format_temporal", "format_value", "format_number"}:
            target = args.get("data_type", "numeric" if name == "format_number" else "date" if name == "format_date" else "timestamp")
            if name == "format_value" and args["formatting"] is None:
                return source
            options = args.get("formatting") if name == "format_value" else {
                key: item for key, item in args.items() if key not in {"expression", "data_type"}}
            if name == "format_number":
                options = dict(options, decimal_places=options.get("decimal_places") or 0)
            return self._format(source, target, options)
        raise SpecificationValidationError(f"{name}: native scalar implementation is unavailable")

    def _format(self, source, target, options):
        from sqlglot import exp
        from sqlglot.time import format_time
        from sqlglot.dialects.spark import Spark

        specification = target if isinstance(target, dict) else {"name": target}
        target = specification["name"].lower()
        target = {"str": "string", "text": "string", "int": "integer", "number": "numeric",
                  "decimal": "numeric", "datetime": "timestamp", "bool": "boolean"}.get(target, target)
        if isinstance(options, str):
            options = {"pattern" if target in {"date", "timestamp"} else "case": options}
        allowed = {"pattern", "decimal_places", "grouping", "prefix", "suffix", "null_value",
                   "case", "trim", "collapse_whitespace", "blank_as_null", "expression", "data_type", "formatting"}
        if set(options) - allowed:
            raise SpecificationValidationError(f"format: unknown options {sorted(set(options) - allowed)}")
        if target in {"date", "timestamp"}:
            pattern = options.get("pattern", "%Y-%m-%d" if target == "date" else "%Y-%m-%d %H:%M:%S")
            spark_pattern = format_time(pattern, Spark.INVERSE_TIME_MAPPING, Spark.INVERSE_TIME_TRIE)
            result = f"DATE_FORMAT({source}, {exp.Literal.string(spark_pattern).sql(dialect='spark')})"
        elif target in {"numeric", "integer"}:
            decimals = options.get("decimal_places", specification.get("scale", 6) if target == "numeric" else 0)
            if options.get("grouping"):
                result = f"FORMAT_NUMBER({source}, {decimals if decimals is not None else 2})"
            elif decimals is not None:
                result = f"CAST(TRY_CAST({source} AS DECIMAL(38, {decimals})) AS STRING)"
            else:
                result = f"CAST({source} AS STRING)"
            result = f"(CASE WHEN {result} IS NULL THEN NULL ELSE CONCAT({self._literal(options.get('prefix', ''))}, {result}, {self._literal(options.get('suffix', ''))}) END)"
        elif target == "string":
            result = self.render("format_string", {"expression": {"literal": ""}, **options, "_source": source})
        elif target == "boolean":
            result = f"CAST({source} AS STRING)"
        else:
            raise SpecificationValidationError(f"format: unsupported data_type {target!r}")
        if options.get("null_value") is not None:
            result = f"COALESCE({result}, {self._literal(options['null_value'])})"
        return result