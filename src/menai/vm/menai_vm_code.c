/*
 * menai_vm_code.c — MenaiCodeObject lifetime management.
 */
#define _POSIX_C_SOURCE 200809L
#include <stdlib.h>
#include <string.h>

#include "menai_vm_c.h"

/*
 * menai_code_object_count — count instructions and code objects in a tree.
 *
 * Walks the tree depth-first in the same order as the Python-side
 * menai_render.menai_render_walk.walk_code_objects, so the totals agree with
 * the ordinals the bridge assigns during conversion.
 */
void
menai_code_object_count(MenaiCodeObject *co, size_t *out_instr, size_t *out_code)
{
    *out_instr += (size_t)co->code_len;
    *out_code += 1;

    for (ssize_t i = 0; i < co->nchildren; i++) {
        menai_code_object_count(co->children[i], out_instr, out_code);
    }
}

void
menai_code_object_final(MenaiVMState *vs, MenaiCodeObject *co)
{
    for (ssize_t i = 0; i < co->nconst; i++) {
        menai_value_release(vs, co->constants[i]);
    }

    free(co->constants);

    for (int i = 0; i < co->njt; i++) {
        free(co->jump_tables[i].targets);
    }

    free(co->jump_tables);

    for (ssize_t i = 0; i < co->nparam_names; i++) {
        free(co->param_names[i]);
    }

    free(co->param_names);

    for (ssize_t i = 0; i < co->nchildren; i++) {
        menai_code_object_release(vs, co->children[i]);
    }

    free(co->children);
    free(co->instrs);
    free(co->name);
    free(co->source_file);
    MENAI_CLEAR_MAGIC(co);
    free(co);
}
