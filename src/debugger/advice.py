def get_advice_for_cluster(cluster_name, target_func):
    """
    Returns a list of steps that can be used by the LLM to help debug different types of errors
    """

    if cluster_name == "memcpy_src":
        return [
            "Do the variable values provided show that the source pointer is NULL or invalid?",
            "Do the variable values provided show that the source region is allocated sufficient space for the copy?",
            # "If the allocated size is not sufficient, do the variable values provided indicate the copy size larger than seems necessary?",
            f"If the allocated size is not sufficient and the copy size seems correct, are there any checks in {target_func} that would prevent the failing line from being called?",
            "If the allocated size is sufficient, do the variable values provided indicate that pointer arithmetic can cause the readable region of the source pointer to be invalid or smaller than the copy size?",
        ]
    elif cluster_name == "memcpy_dst":
        return [
            "Do the variable values provided show that the destination pointer is NULL or invalid?",
            "Do the variable values provided show that the destination region is allocated sufficient space for the copy?",
            "If the allocated size is not sufficient, do the variable values provided indicate the copy size larger than seems necessary?",
            f"If the allocated size is not sufficient and the copy size seems correct, are there any checks in {target_func} that would prevent the failing line from being called?",
            "If the allocated size is sufficient, do the variable values and functiond definitions provided indicate that pointer arithmetic can cause the writeable region of the destination pointer to be invalid or smaller than the copy size?",
        ]
    elif cluster_name == "memcpy_overlap":
        return [
            "Based on the harness definition provided, are the source and destination pointers both directly allocated?",
        ]
    elif cluster_name == "arithmetic_overflow":
        return [
            "Based on the variable values provided, which variable in the equation is responsible for causing the overflow?",
            "If the responsible variable is initially defined in the harness, what constraints must be added to prevent the overflow?",
            "If the responsible variable was not defined in a harness or stub, was it returned from an undefined function or set as a global variable?",
        ]
    elif cluster_name == "deref_null":
        return [
            "Based on the variable values provided, does the NULL pointer have a precondition that prevents it from being NULL?",
            "If there is such a precondition, is the pointer variable ever assigned the return value of an undefined function?",
            "If the pointer variable is not set to the return value of an undefined function, are there any other assignments to that variable that could result in a NULL value?",
        ]
    elif cluster_name == "deref_arr_oob":
        return [
            "Based on the variable values provided, is the offset read from the pointer greater than the allocated size of the pointer?",
            "If the offset is larger than the allocated size of the pointer, does the allocated size have a reasonable lower bound?",
            "If the offset is larger than the allocated size of the pointer and there is a reasonable lower bound on the allocated size, does the offset have a reasonable upper bound?",
            "If the offset value should be within the allocated pointer size based on the variable values provided, is there any arithmetic performed that would cause the offset to become larger than the allocated pointer size?",
        ]
    elif cluster_name == "deref_obj_oob":
        # NOTE: The way this is phrased only makes sense if the LLM has a definition for each data type in the harness, which currently is not the case
        return [
            "Based on the variable values provided, is the object allocated sufficient space for the field it attempts to read?"
        ]
    else:
        return []