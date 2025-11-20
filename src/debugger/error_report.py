class CBMCError:
    
    def __init__(self, error_obj):

        self.line = error_obj.get('line', '')
        self.msg = error_obj.get('msg', '')
        self.func = error_obj.get('function', '')
        self.file = error_obj.get('file', None)
        self.stack = error_obj.get('stack', None)
        self.vars = error_obj.get('harness_vars', None)
        self.is_built_in = error_obj.get('is_built_in', '')
        
        self.cluster = ""
        self.error_id = ""

        # Reporting vars
        self.attempts = -1
        self.added_precons = None
        self.indirectly_resolved = []
        self.tokens = {
            'input': 0,
            'output': 0, 
            'cached': 0
        }
        self.responses = []
    
        self.processed = False # Set to true if the LLM ever tries to directly address the error
        self.resolved_by = None # Should only ever be not None if this error was resolved indirectly

    def __str__(self):
        return f"{self.msg} @ {self.func} Line {self.line}"

    def update(self, new_error):
        """
        Updates the error object with new values
        """
        
        # There are cases where we might want this check, as sometimes a function can be called from two different places with two different variable contexts
        # But for now, we will assume that the same error ID means the same context
        
        # if self.vars.keys() != new_error.vars.keys():
        #     raise ValueError("Cannot update error with different variable keys")
        
        self.vars = new_error.vars
    
    def get_err_report(self):
        return {
            'function': self.func,
            'line': self.line,
            'msg': self.msg,
            'attempts': self.attempts,
            'added_precons': self.added_precons,
            'indirectly_resolved': self.indirectly_resolved,
            'resolved_by': self.resolved_by,
            'tokens': self.tokens,
            'responses': self.responses
        }


class ErrorReport:

    # Reqs:
    # A way to compare reports and get any new errors
    # A "fetch next error" (done)
    # A way to check if an error got removed (done)
    # A method to dump the errors into the report format (done)
    # A method to update the variable values of a particular error after the most recent run

    CLUSTER_ORDER = [
            'deref_null',
            'deref_arr_oob',
            'deref_obj_oob',
            'memcpy_src',
            'memcpy_dest',
            'memcpy_overlap',
            'misc'
        ]
    
    def __init__(self, errors):

        # This is the clustered set of errors we will actually be updating
        self.errors_by_cluster = { cluster: set([key for key in errs.keys()]) for cluster, errs in errors.items() }

        # This is meant to be a static dictionary mapping the actual instances of the error class
        self.errors_by_id = {key: CBMCError(err_obj) for cluster in errors.values() for key, err_obj in cluster.items()}

        self.errors_by_line = {}

        for err in self.errors_by_id.values():
            self.errors_by_line.setdefault(f"{err.func}:{err.line}", []).append(err)

        # This is a dynamic set to track error hashes that have not yet been resolved
        self.unresolved_errs = set(self.errors_by_id.keys())
        self.resolved_errs = set()
        self.failed_errs = set()
    
    def __contains__(self, error_id):
        return error_id in self.errors_by_id

    def get_next_error(self, errors_to_skip: set):
        """
        Finds the next unresolved error, based on CLUSTER_ORDER
        It may seem inefficient to re-read through all of the errors, but there is always a chance new errors can be added
        """

        for cluster in ErrorReport.CLUSTER_ORDER:
            if cluster in self.errors_by_cluster and len(self.errors_by_cluster[cluster]) > 0:
                for error_id in  self.errors_by_cluster[cluster]:
                    print("errors_to_skip: ", errors_to_skip)
                    if error_id in errors_to_skip:
                        continue
                    if error_id in self.unresolved_errs:
                        self.get_err(error_id).processed = True
                        return cluster, error_id, self.get_err(error_id)
        
        return None, None, None
    
    def summarize_errors(self):

        return {
            'total': len(self.errors_by_id),
            **{ cluster: [str(err) for err in self.errors_by_cluster[cluster]] for cluster in self.errors_by_cluster.keys()}
        }
    
    def get_err(self, error_id):
        return self.errors_by_id[error_id]

    def update_target_err(self, target_error_id, new_errors):
        """
        Checks if the error was resolved and returns True if it was
        If it was not resolved, updates the variable values of the target error and returns False
        """

        if target_error_id not in new_errors:
            # Error was resolved
            return True
        
        else:
            # If error was not resolved, update the variable values
            self.get_err(target_error_id).update(new_errors.get_err(target_error_id))
            return False

    def generate_results_report(self):
        """
        Generates a report of the results of the error analysis
        """

        results_report = {
            'initial_errors': self.summarize_errors(),
            'processed_errors': {
                'success': dict(),
                'failure': dict()
            },
            'preconditions_added': [],
        }

        for err_id, err in self.errors_by_id.items():
            if not err.processed:
                continue

            if err_id in self.failed_errs or err.resolved_by is not None:
                results_report['processed_errors']['failure'][err_id] = err.get_err_report()
            else:
                results_report['processed_errors']['success'][err_id] = err.get_err_report()
                results_report['preconditions_added'].extend(err.added_precons)

            return results_report
        
        
