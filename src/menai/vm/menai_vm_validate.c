/*
 * menai_vm_validate.c — C bytecode validator for the Menai VM.
 *
 * Validation passes:
 *   1. Structure — at least one instruction, all opcodes valid.
 *   2. Indices — per-instruction bounds checks on constants, names, code
 *      objects, registers, and jump targets.
 *   3. Control flow — all reachable basic blocks must end with a terminal
 *      opcode or have successors.
 *   4. Initialization — definite assignment analysis ensuring every variable
 *      is initialized before use, plus closure-map tracking for PATCH_CLOSURE.
 */
#define _POSIX_C_SOURCE 200809L
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

#include "menai_vm_c.h"
#include "menai_vm_opcodes.h"

/* Instruction encoding — must match menai_vm_c.c */
#define V_OPCODE_SHIFT 48
#define V_DEST_SHIFT 36
#define V_SRC0_SHIFT 24
#define V_SRC1_SHIFT 12
#define V_FIELD_MASK 0xFFFu
#define V_OPCODE_MASK 0xFFFFu

/* Highest valid opcode (OP_SWITCH_INTEGER) */
#define V_HIGHEST_OPCODE 323

/*
 * Initialized-slot bitmask: 4096 bits = 64 uint64_t words.
 * Slot indices are 12-bit (max 4095).
 */
#define V_SLOT_WORDS 64
#define V_MAX_SLOTS 4096

typedef struct {
    uint64_t init[V_SLOT_WORDS];
    int *closure_map; /* slot -> child index, or -1; sized to total_slots */
} InitState;

/* Validation error type — matches Python ValidationErrorType */
typedef enum {
    VERR_INVALID_JUMP_TARGET = 0,
    VERR_INDEX_OUT_OF_BOUNDS,
    VERR_MISSING_RETURN,
    VERR_INVALID_OPCODE,
    VERR_INVALID_VARIABLE_ACCESS,
    VERR_UNINITIALIZED_VARIABLE
} VErrorType;

static int is_no_dest_opcode(int opcode);
static int is_terminal_opcode(int opcode);
static void set_error(MenaiValidationError *err, VErrorType type, const char *msg, int instr_idx, int opcode);
static int validate_structure(MenaiCodeObject *co, MenaiValidationError *err);
static int validate_indices(MenaiCodeObject *co, MenaiValidationError *err);
static int validate_control_flow(MenaiCodeObject *co, MenaiValidationError *err);
static int validate_initialization(MenaiCodeObject *co, MenaiValidationError *err);

/*
 * is_no_dest_opcode — returns 1 if the opcode does not write a destination
 * register (dest field is unused).
 */
static int
is_no_dest_opcode(int opcode)
{
    switch (opcode) {
    case OP_TAIL_CALL:
    case OP_TAIL_APPLY:
    case OP_PATCH_CLOSURE:
    case OP_RETURN:
    case OP_JUMP:
    case OP_JUMP_IF_FALSE:
    case OP_JUMP_IF_TRUE:
    case OP_SWITCH_INTEGER:
    case OP_RAISE_ERROR:
        return 1;
    default:
        return 0;
    }
}

/*
 * is_terminal_opcode — returns 1 if the opcode ends control flow (no
 * successors).  Used by both control-flow and initialization passes.
 */
static int
is_terminal_opcode(int opcode)
{
    switch (opcode) {
    case OP_RETURN:
    case OP_RAISE_ERROR:
    case OP_TAIL_CALL:
    case OP_TAIL_APPLY:
        return 1;
    default:
        return 0;
    }
}

static void
set_error(MenaiValidationError *err, VErrorType type, const char *msg,
          int instr_idx, int opcode)
{
    if (err) {
        err->error_type = (int)type;
        err->message = strdup(msg);
        err->instruction_index = instr_idx;
        err->opcode = opcode;
    }
}

/*
 * validate_structure — must have at least one instruction and all opcodes
 * must be valid (<= V_HIGHEST_OPCODE).
 */
static int
validate_structure(MenaiCodeObject *co, MenaiValidationError *err)
{
    if (co->code_len < 1) {
        set_error(err, VERR_INVALID_OPCODE,
                  "Code object has no instructions", -1, -1);
        return MENAI_ERR_MISSING_RETURN;
    }

    for (int i = 0; i < co->code_len; i++) {
        uint64_t word = co->instrs[i];
        int opcode = (int)((word >> V_OPCODE_SHIFT) & V_OPCODE_MASK);
        if (opcode < 0 || opcode > V_HIGHEST_OPCODE) {
            char buf[128];
            snprintf(buf, sizeof(buf),
                     "Invalid opcode value: %d", opcode);
            set_error(err, VERR_INVALID_OPCODE,
                      buf, i, opcode);
            return MENAI_ERR_UNIMPLEMENTED_OPCODE;
        }
    }

    return MENAI_OK;
}

/*
 * validate_indices — per-instruction bounds checks.
 */
static int
validate_indices(MenaiCodeObject *co, MenaiValidationError *err)
{
    int total_slots = co->local_count + co->outgoing_arg_slots;
    int code_len = co->code_len;

    for (int i = 0; i < code_len; i++) {
        uint64_t word = co->instrs[i];
        int opcode = (int)((word >> V_OPCODE_SHIFT) & V_OPCODE_MASK);
        int dest = (int)((word >> V_DEST_SHIFT) & V_FIELD_MASK);
        int src0 = (int)((word >> V_SRC0_SHIFT) & V_FIELD_MASK);
        int src1 = (int)((word >> V_SRC1_SHIFT) & V_FIELD_MASK);
        int src2 = (int)(word & V_FIELD_MASK);

        /* LOAD_CONST: src0 must be < nconst */
        if (opcode == OP_LOAD_CONST) {
            if (src0 < 0 || src0 >= (int)co->nconst) {
                char buf[256];
                snprintf(buf, sizeof(buf),
                         "Constant index %d out of bounds (pool size: %zd)",
                         src0, co->nconst);
                set_error(err, VERR_INDEX_OUT_OF_BOUNDS,
                          buf, i, opcode);
                return MENAI_ERR_INDEX_OUT_OF_RANGE;
            }
        }

        /* LOAD_NAME: src0 must be < nnames */
        if (opcode == OP_LOAD_NAME) {
            if (src0 < 0 || src0 >= (int)co->nnames) {
                char buf[256];
                snprintf(buf, sizeof(buf),
                         "Name index %d out of bounds (pool size: %zd)",
                         src0, co->nnames);
                set_error(err, VERR_INDEX_OUT_OF_BOUNDS,
                          buf, i, opcode);
                return MENAI_ERR_INDEX_OUT_OF_RANGE;
            }
        }

        /* MAKE_CLOSURE: src0 must be < nchildren */
        if (opcode == OP_MAKE_CLOSURE) {
            if (src0 < 0 || src0 >= (int)co->nchildren) {
                char buf[256];
                snprintf(buf, sizeof(buf),
                         "Code object index %d out of bounds (pool size: %zd)",
                         src0, co->nchildren);
                set_error(err, VERR_INDEX_OUT_OF_BOUNDS,
                          buf, i, opcode);
                return MENAI_ERR_INDEX_OUT_OF_RANGE;
            }
        }

        /* MOVE: src0 must be < total_slots */
        if (opcode == OP_MOVE) {
            if (src0 < 0 || src0 >= total_slots) {
                char buf[256];
                snprintf(buf, sizeof(buf),
                         "MOVE source %d out of bounds (total_slots: %d)",
                         src0, total_slots);
                set_error(err, VERR_INVALID_VARIABLE_ACCESS,
                          buf, i, opcode);
                return MENAI_ERR_UNDEFINED_VARIABLE;
            }
        }

        /* Dest register bounds for all dest-writing opcodes */
        if (!is_no_dest_opcode(opcode)) {
            if (dest < 0 || dest >= total_slots) {
                char buf[256];
                snprintf(buf, sizeof(buf),
                         "Destination register %d out of bounds (total_slots: %d)",
                         dest, total_slots);
                set_error(err, VERR_INVALID_VARIABLE_ACCESS,
                          buf, i, opcode);
                return MENAI_ERR_UNDEFINED_VARIABLE;
            }
        }

        /* CALL/TAIL_CALL/APPLY/TAIL_APPLY: src0 (func register) < local_count */
        if (opcode == OP_CALL || opcode == OP_TAIL_CALL ||
            opcode == OP_APPLY || opcode == OP_TAIL_APPLY) {
            if (src0 < 0 || src0 >= co->local_count) {
                char buf[256];
                snprintf(buf, sizeof(buf),
                         "Function register %d out of bounds (local_count: %d)",
                         src0, co->local_count);
                set_error(err, VERR_INVALID_VARIABLE_ACCESS,
                          buf, i, opcode);
                return MENAI_ERR_UNDEFINED_VARIABLE;
            }
        }

        /* RAISE_ERROR: src0 (message register) < local_count */
        if (opcode == OP_RAISE_ERROR) {
            if (src0 < 0 || src0 >= co->local_count) {
                char buf[256];
                snprintf(buf, sizeof(buf),
                         "RAISE_ERROR message register %d out of bounds (local_count: %d)",
                         src0, co->local_count);
                set_error(err, VERR_INVALID_VARIABLE_ACCESS,
                          buf, i, opcode);
                return MENAI_ERR_UNDEFINED_VARIABLE;
            }
        }

        /* APPLY/TAIL_APPLY: src1 (arg_list register) < local_count */
        if (opcode == OP_APPLY || opcode == OP_TAIL_APPLY) {
            if (src1 < 0 || src1 >= co->local_count) {
                char buf[256];
                snprintf(buf, sizeof(buf),
                         "APPLY arg_list register %d out of bounds (local_count: %d)",
                         src1, co->local_count);
                set_error(err, VERR_INVALID_VARIABLE_ACCESS,
                          buf, i, opcode);
                return MENAI_ERR_UNDEFINED_VARIABLE;
            }
        }

        /* PATCH_CLOSURE: src0 and src2 must be < total_slots; src1 >= 0 */
        if (opcode == OP_PATCH_CLOSURE) {
            if (src0 < 0 || src0 >= total_slots) {
                char buf[256];
                snprintf(buf, sizeof(buf),
                         "PATCH_CLOSURE src0 (closure) register %d out of bounds (total_slots: %d)",
                         src0, total_slots);
                set_error(err, VERR_INVALID_VARIABLE_ACCESS,
                          buf, i, opcode);
                return MENAI_ERR_UNDEFINED_VARIABLE;
            }

            if (src2 < 0 || src2 >= total_slots) {
                char buf[256];
                snprintf(buf, sizeof(buf),
                         "PATCH_CLOSURE src2 (value) register %d out of bounds (total_slots: %d)",
                         src2, total_slots);
                set_error(err, VERR_INVALID_VARIABLE_ACCESS,
                          buf, i, opcode);
                return MENAI_ERR_UNDEFINED_VARIABLE;
            }

            if (src1 < 0) {
                char buf[256];
                snprintf(buf, sizeof(buf),
                         "PATCH_CLOSURE capture_idx (src1) %d is negative",
                         src1);
                set_error(err, VERR_INVALID_VARIABLE_ACCESS,
                          buf, i, opcode);
                return MENAI_ERR_UNDEFINED_VARIABLE;
            }
        }

        /* JUMP: src0 (target) must be < code_len */
        if (opcode == OP_JUMP) {
            if (src0 < 0 || src0 >= code_len) {
                char buf[256];
                snprintf(buf, sizeof(buf),
                         "Jump target %d out of bounds (instruction count: %d)",
                         src0, code_len);
                set_error(err, VERR_INVALID_JUMP_TARGET,
                          buf, i, opcode);
                return MENAI_ERR_MISSING_RETURN;
            }
        }

        /* JUMP_IF_FALSE/JUMP_IF_TRUE: src0 (cond reg) < local_count,
         * src1 (target) < code_len */
        if (opcode == OP_JUMP_IF_FALSE || opcode == OP_JUMP_IF_TRUE) {
            if (src0 < 0 || src0 >= co->local_count) {
                char buf[256];
                snprintf(buf, sizeof(buf),
                         "Condition register %d out of bounds (local_count: %d)",
                         src0, co->local_count);
                set_error(err, VERR_INVALID_VARIABLE_ACCESS,
                          buf, i, opcode);
                return MENAI_ERR_UNDEFINED_VARIABLE;
            }

            if (src1 < 0 || src1 >= code_len) {
                char buf[256];
                snprintf(buf, sizeof(buf),
                         "Jump target %d out of bounds (instruction count: %d)",
                         src1, code_len);
                set_error(err, VERR_INVALID_JUMP_TARGET,
                          buf, i, opcode);
                return MENAI_ERR_MISSING_RETURN;
            }
        }

        /* SWITCH_INTEGER: src0 (scrutinee register) < local_count, src1 (table
         * index) < njt, and every table target < code_len */
        if (opcode == OP_SWITCH_INTEGER) {
            if (src0 < 0 || src0 >= co->local_count) {
                char buf[256];
                snprintf(buf, sizeof(buf),
                         "SWITCH_INTEGER scrutinee register %d out of bounds (local_count: %d)",
                         src0, co->local_count);
                set_error(err, VERR_INVALID_VARIABLE_ACCESS,
                          buf, i, opcode);
                return MENAI_ERR_UNDEFINED_VARIABLE;
            }

            if (src1 < 0 || src1 >= co->njt) {
                char buf[256];
                snprintf(buf, sizeof(buf),
                         "SWITCH_INTEGER jump table index %d out of bounds (tables: %d)",
                         src1, co->njt);
                set_error(err, VERR_INDEX_OUT_OF_BOUNDS,
                          buf, i, opcode);
                return MENAI_ERR_INDEX_OUT_OF_RANGE;
            }

            const MenaiJumpTable *t = &co->jump_tables[src1];
            if (t->default_target < 0 || t->default_target >= code_len) {
                char buf[256];
                snprintf(buf, sizeof(buf),
                         "SWITCH_INTEGER default target %d out of bounds (instructions: %d)",
                         t->default_target, code_len);
                set_error(err, VERR_INVALID_JUMP_TARGET,
                          buf, i, opcode);
                return MENAI_ERR_MISSING_RETURN;
            }

            for (int j = 0; j < t->count; j++) {
                if (t->targets[j] < 0 || t->targets[j] >= code_len) {
                    char buf[256];
                    snprintf(buf, sizeof(buf),
                             "SWITCH_INTEGER target %d out of bounds (instructions: %d)",
                             t->targets[j], code_len);
                    set_error(err, VERR_INVALID_JUMP_TARGET,
                              buf, i, opcode);
                    return MENAI_ERR_MISSING_RETURN;
                }
            }
        }
    }

    return MENAI_OK;
}

/*
 * validate_control_flow — all reachable paths must terminate.
 *
 * Builds a simplified CFG: finds leaders (instruction 0, jump targets,
 * instructions after jumps/returns), builds basic blocks, marks reachable
 * blocks from block 0, and checks that every reachable block either ends
 * with a terminal opcode or has successors.
 */
static int
validate_control_flow(MenaiCodeObject *co, MenaiValidationError *err)
{
    int code_len = co->code_len;
    uint64_t *instrs = co->instrs;

    /* Find leaders */
    char *is_leader = calloc((size_t)code_len, 1);
    if (!is_leader) {
        return MENAI_ERR_NOMEM;
    }

    is_leader[0] = 1;

    for (int i = 0; i < code_len; i++) {
        uint64_t word = instrs[i];
        int opcode = (int)((word >> V_OPCODE_SHIFT) & V_OPCODE_MASK);
        int src0 = (int)((word >> V_SRC0_SHIFT) & V_FIELD_MASK);
        int src1 = (int)((word >> V_SRC1_SHIFT) & V_FIELD_MASK);

        if (opcode == OP_JUMP) {
            if (src0 >= 0 && src0 < code_len) {
                is_leader[src0] = 1;
            }
        }

        if (opcode == OP_JUMP_IF_FALSE || opcode == OP_JUMP_IF_TRUE) {
            if (src1 >= 0 && src1 < code_len) {
                is_leader[src1] = 1;
            }

            if (i + 1 < code_len) {
                is_leader[i + 1] = 1;
            }
        }

        if (opcode == OP_SWITCH_INTEGER) {
            if (src1 >= 0 && src1 < co->njt) {
                const MenaiJumpTable *t = &co->jump_tables[src1];
                for (int j = 0; j < t->count; j++) {
                    if (t->targets[j] >= 0 && t->targets[j] < code_len) {
                        is_leader[t->targets[j]] = 1;
                    }
                }

                if (t->default_target >= 0 && t->default_target < code_len) {
                    is_leader[t->default_target] = 1;
                }
            }

            if (i + 1 < code_len) {
                is_leader[i + 1] = 1;
            }
        }

        if (opcode == OP_RETURN || opcode == OP_RAISE_ERROR ||
            opcode == OP_TAIL_CALL || opcode == OP_TAIL_APPLY) {
            if (i + 1 < code_len) {
                is_leader[i + 1] = 1;
            }
        }
    }

    /* Build block list: each block has start, end (inclusive) */
    int max_blocks = code_len;
    int *block_start = malloc(sizeof(int) * (size_t)max_blocks);
    int *block_end = malloc(sizeof(int) * (size_t)max_blocks);
    char *block_visited = calloc((size_t)max_blocks, 1);

    if (!block_start || !block_end || !block_visited) {
        free(is_leader);
        free(block_start);
        free(block_end);
        free(block_visited);
        return MENAI_ERR_NOMEM;
    }

    /* Map instruction index to block index */
    int *instr_to_block = malloc(sizeof(int) * (size_t)code_len);
    if (!instr_to_block) {
        free(is_leader);
        free(block_start);
        free(block_end);
        free(block_visited);
        return MENAI_ERR_NOMEM;
    }

    int nblocks = 0;
    for (int i = 0; i < code_len; i++) {
        if (is_leader[i]) {
            block_start[nblocks] = i;
            /* Find end: next leader - 1, or code_len - 1 */
            int end = code_len - 1;
            for (int j = i + 1; j < code_len; j++) {
                if (is_leader[j]) {
                    end = j - 1;
                    break;
                }
            }
            block_end[nblocks] = end;
            for (int k = i; k <= end; k++) {
                instr_to_block[k] = nblocks;
            }
            nblocks++;
        }
    }

    /* Mark reachable blocks from block 0 using a worklist */
    int *worklist = malloc(sizeof(int) * (size_t)nblocks);
    if (!worklist) {
        free(is_leader);
        free(block_start);
        free(block_end);
        free(block_visited);
        free(instr_to_block);
        return MENAI_ERR_NOMEM;
    }

    int wl_head = 0;
    int wl_tail = 0;
    worklist[wl_tail++] = 0;
    block_visited[0] = 1;

    while (wl_head < wl_tail) {
        int blk = worklist[wl_head++];
        int end_idx = block_end[blk];
        uint64_t word = instrs[end_idx];
        int opcode = (int)((word >> V_OPCODE_SHIFT) & V_OPCODE_MASK);
        int src0 = (int)((word >> V_SRC0_SHIFT) & V_FIELD_MASK);
        int src1 = (int)((word >> V_SRC1_SHIFT) & V_FIELD_MASK);

        /* Compute successors of this block's last instruction */
        if (opcode == OP_SWITCH_INTEGER) {
            if (src1 >= 0 && src1 < co->njt) {
                const MenaiJumpTable *t = &co->jump_tables[src1];
                for (int j = 0; j <= t->count; j++) {
                    int succ = (j < t->count) ? t->targets[j] : t->default_target;
                    if (succ < 0 || succ >= code_len) {
                        continue;
                    }

                    int succ_blk = instr_to_block[succ];
                    if (!block_visited[succ_blk]) {
                        block_visited[succ_blk] = 1;
                        worklist[wl_tail++] = succ_blk;
                    }
                }
            }

            continue;
        }

        if (is_terminal_opcode(opcode)) {
            continue;
        }

        int succs[2];
        int nsuccs = 0;

        if (opcode == OP_JUMP) {
            succs[nsuccs++] = src0;
        } else if (opcode == OP_JUMP_IF_FALSE || opcode == OP_JUMP_IF_TRUE) {
            succs[nsuccs++] = src1;
            if (end_idx + 1 < code_len) {
                succs[nsuccs++] = end_idx + 1;
            }
        } else {
            if (end_idx + 1 < code_len) {
                succs[nsuccs++] = end_idx + 1;
            }
        }

        for (int s = 0; s < nsuccs; s++) {
            int succ_instr = succs[s];
            if (succ_instr < 0 || succ_instr >= code_len) {
                continue;
            }

            int succ_blk = instr_to_block[succ_instr];
            if (!block_visited[succ_blk]) {
                block_visited[succ_blk] = 1;
                worklist[wl_tail++] = succ_blk;
            }
        }
    }

    /* Check that every reachable block ends properly */
    int result = MENAI_OK;
    for (int b = 0; b < nblocks; b++) {
        if (!block_visited[b]) {
            continue;
        }

        int end_idx = block_end[b];
        uint64_t word = instrs[end_idx];
        int opcode = (int)((word >> V_OPCODE_SHIFT) & V_OPCODE_MASK);

        int has_successors = 0;
        if (!is_terminal_opcode(opcode)) {
            if (opcode == OP_SWITCH_INTEGER) {
                int src1 = (int)((word >> V_SRC1_SHIFT) & V_FIELD_MASK);
                if (src1 >= 0 && src1 < co->njt) {
                    const MenaiJumpTable *t = &co->jump_tables[src1];
                    if (t->count > 0 || (t->default_target >= 0 && t->default_target < code_len)) {
                        has_successors = 1;
                    }
                }
            } else if (opcode == OP_JUMP) {
                int src0 = (int)((word >> V_SRC0_SHIFT) & V_FIELD_MASK);
                if (src0 >= 0 && src0 < code_len) {
                    has_successors = 1;
                }
            } else if (opcode == OP_JUMP_IF_FALSE || opcode == OP_JUMP_IF_TRUE) {
                int src1 = (int)((word >> V_SRC1_SHIFT) & V_FIELD_MASK);
                if (src1 >= 0 && src1 < code_len) {
                    has_successors = 1;
                }
                if (end_idx + 1 < code_len) {
                    has_successors = 1;
                }
            } else {
                if (end_idx + 1 < code_len) {
                    has_successors = 1;
                }
            }
        }

        if (!is_terminal_opcode(opcode) && !has_successors) {
            char buf[256];
            snprintf(buf, sizeof(buf),
                     "Control flow falls off end of block at instruction %d",
                     end_idx);
            set_error(err, VERR_MISSING_RETURN,
                      buf, end_idx, opcode);
            result = MENAI_ERR_MISSING_RETURN;
            break;
        }
    }

    free(is_leader);
    free(block_start);
    free(block_end);
    free(block_visited);
    free(instr_to_block);
    free(worklist);
    return result;
}

static void
init_state_clear(InitState *s, int total_slots)
{
    memset(s->init, 0, sizeof(s->init));
    for (int i = 0; i < total_slots; i++) {
        s->closure_map[i] = -1;
    }
}

static void
init_state_set_bit(InitState *s, int slot)
{
    if (slot >= 0 && slot < V_MAX_SLOTS) {
        s->init[slot / 64] |= (uint64_t)1 << (slot % 64);
    }
}

static int
init_state_get_bit(const InitState *s, int slot)
{
    if (slot < 0 || slot >= V_MAX_SLOTS) {
        return 0;
    }

    return (s->init[slot / 64] >> (slot % 64)) & 1;
}

static void
init_state_union(InitState *dst, const InitState *src)
{
    for (int i = 0; i < V_SLOT_WORDS; i++) {
        dst->init[i] |= src->init[i];
    }
}

static void
init_state_intersect(InitState *dst, const InitState *src, int total_slots)
{
    for (int i = 0; i < V_SLOT_WORDS; i++) {
        dst->init[i] &= src->init[i];
    }

    for (int i = 0; i < total_slots; i++) {
        if (src->closure_map[i] != dst->closure_map[i]) {
            dst->closure_map[i] = -1;
        }
    }
}

static int
init_state_equal(const InitState *a, const InitState *b, int total_slots)
{
    if (memcmp(a->init, b->init, sizeof(a->init)) != 0) {
        return 0;
    }

    for (int i = 0; i < total_slots; i++) {
        if (a->closure_map[i] != b->closure_map[i]) {
            return 0;
        }
    }

    return 1;
}

static void
init_state_copy(InitState *dst, const InitState *src, int total_slots)
{
    memcpy(dst->init, src->init, sizeof(dst->init));
    memcpy(dst->closure_map, src->closure_map, (size_t)total_slots * sizeof(int));
}

/*
 * get_successors — compute successor instruction indices for a given
 * instruction.  Returns the count and fills succs[] (max 2).
 */
static int
get_successors(int instr_idx, int opcode, int src0, int src1, int code_len, int succs[2])
{
    if (is_terminal_opcode(opcode)) {
        return 0;
    }

    int nsuccs = 0;

    if (opcode == OP_JUMP) {
        succs[nsuccs++] = src0;
    } else if (opcode == OP_JUMP_IF_FALSE || opcode == OP_JUMP_IF_TRUE) {
        succs[nsuccs++] = src1;
        if (instr_idx + 1 < code_len) {
            succs[nsuccs++] = instr_idx + 1;
        }
    } else {
        if (instr_idx + 1 < code_len) {
            succs[nsuccs++] = instr_idx + 1;
        }
    }

    return nsuccs;
}

/*
 * validate_initialization — definite assignment analysis.
 *
 * Uses a worklist algorithm to track which slots are definitely initialized
 * at each instruction, plus a closure map (slot -> child index) for
 * PATCH_CLOSURE validation.
 */
static int
validate_initialization(MenaiCodeObject *co, MenaiValidationError *err)
{
    int code_len = co->code_len;
    int total_slots = co->local_count + co->outgoing_arg_slots;
    if (total_slots < 1) { total_slots = 1; }
    uint64_t *instrs = co->instrs;

    /* Build initial initialized set: params + captured slots */
    InitState initial;
    initial.closure_map = malloc((size_t)total_slots * sizeof(int));
    if (!initial.closure_map) {
        return MENAI_ERR_NOMEM;
    }
    init_state_clear(&initial, total_slots);

    if (co->param_count > 0) {
        for (int i = 0; i < co->param_count && i < V_MAX_SLOTS; i++) {
            init_state_set_bit(&initial, i);
        }
    }

    if (co->ncap > 0) {
        for (ssize_t i = 0; i < co->ncap; i++) {
            int slot = co->param_count + (int)i;
            if (slot < V_MAX_SLOTS) {
                init_state_set_bit(&initial, slot);
            }
        }
    }

    /*
     * Allocate per-instruction state.  InitState is small (512 bytes for
     * the bitmask + one pointer); the closure_map is allocated separately
     * per instruction, sized to total_slots.
     */
    InitState *states = calloc((size_t)code_len, sizeof(InitState));
    if (!states) {
        free(initial.closure_map);
        return MENAI_ERR_NOMEM;
    }

    /* Allocate and initialize closure maps for each instruction */
    for (int i = 0; i < code_len; i++) {
        states[i].closure_map = malloc((size_t)total_slots * sizeof(int));
        if (!states[i].closure_map) {
            for (int j = 0; j < i; j++) {
                free(states[j].closure_map);
            }
            free(states);
            free(initial.closure_map);
            return MENAI_ERR_NOMEM;
        }
        for (int j = 0; j < total_slots; j++) {
            states[i].closure_map[j] = -1;
        }
    }

    /* Set initial state for instruction 0 */
    init_state_copy(&states[0], &initial, total_slots);

    /* Worklist */
    int *worklist = malloc(sizeof(int) * (size_t)code_len);
    if (!worklist) {
        for (int i = 0; i < code_len; i++) { free(states[i].closure_map); }
        free(states);
        free(initial.closure_map);
        return MENAI_ERR_NOMEM;
    }

    char *in_worklist = calloc((size_t)code_len, 1);
    if (!in_worklist) {
        for (int i = 0; i < code_len; i++) { free(states[i].closure_map); }
        free(states);
        free(initial.closure_map);
        free(worklist);
        return MENAI_ERR_NOMEM;
    }

    char *visited = calloc((size_t)code_len, 1);
    if (!visited) {
        for (int i = 0; i < code_len; i++) { free(states[i].closure_map); }
        free(states);
        free(initial.closure_map);
        free(worklist);
        free(in_worklist);
        return MENAI_ERR_NOMEM;
    }

    int wl_head = 0;
    int wl_tail = 0;
    worklist[wl_tail++] = 0;
    in_worklist[0] = 1;
    visited[0] = 1;

    int result = MENAI_OK;

    while (wl_head < wl_tail) {
        int instr_idx = worklist[wl_head++];
        in_worklist[instr_idx] = 0;

        uint64_t word = instrs[instr_idx];
        int opcode = (int)((word >> V_OPCODE_SHIFT) & V_OPCODE_MASK);
        int dest = (int)((word >> V_DEST_SHIFT) & V_FIELD_MASK);
        int src0 = (int)((word >> V_SRC0_SHIFT) & V_FIELD_MASK);
        int src1 = (int)((word >> V_SRC1_SHIFT) & V_FIELD_MASK);
        int src2 = (int)(word & V_FIELD_MASK);

        InitState *cur = &states[instr_idx];

        /* Check MOVE: source must be initialized */
        if (opcode == OP_MOVE) {
            if (!init_state_get_bit(cur, src0)) {
                char buf[256];
                snprintf(buf, sizeof(buf),
                         "MOVE source register %d may be uninitialized", src0);
                set_error(err, VERR_UNINITIALIZED_VARIABLE,
                          buf, instr_idx, opcode);
                result = MENAI_ERR_UNDEFINED_VARIABLE;
                goto done;
            }
        }

        /* Check RETURN: source must be initialized */
        if (opcode == OP_RETURN) {
            if (!init_state_get_bit(cur, src0)) {
                char buf[256];
                snprintf(buf, sizeof(buf),
                         "RETURN source register %d may be uninitialized", src0);
                set_error(err, VERR_UNINITIALIZED_VARIABLE,
                          buf, instr_idx, opcode);
                result = MENAI_ERR_UNDEFINED_VARIABLE;
                goto done;
            }
        }

        /* Check RAISE_ERROR: message register must be initialized */
        if (opcode == OP_RAISE_ERROR) {
            if (!init_state_get_bit(cur, src0)) {
                char buf[256];
                snprintf(buf, sizeof(buf),
                         "RAISE_ERROR message register %d may be uninitialized",
                         src0);
                set_error(err, VERR_UNINITIALIZED_VARIABLE,
                          buf, instr_idx, opcode);
                result = MENAI_ERR_UNDEFINED_VARIABLE;
                goto done;
            }
        }

        /* Check SWITCH_INTEGER: scrutinee register must be initialized */
        if (opcode == OP_SWITCH_INTEGER) {
            if (!init_state_get_bit(cur, src0)) {
                char buf[256];
                snprintf(buf, sizeof(buf),
                         "SWITCH_INTEGER scrutinee register %d may be uninitialized",
                         src0);
                set_error(err, VERR_UNINITIALIZED_VARIABLE,
                          buf, instr_idx, opcode);
                result = MENAI_ERR_UNDEFINED_VARIABLE;
                goto done;
            }
        }

        /* Check PATCH_CLOSURE */
        if (opcode == OP_PATCH_CLOSURE) {
            /* src0 (closure register) must be initialized */
            if (!init_state_get_bit(cur, src0)) {
                char buf[256];
                snprintf(buf, sizeof(buf),
                         "PATCH_CLOSURE target slot %d may be uninitialized",
                         src0);
                set_error(err, VERR_UNINITIALIZED_VARIABLE,
                          buf, instr_idx, opcode);
                result = MENAI_ERR_UNDEFINED_VARIABLE;
                goto done;
            }

            /* src2 (value register) must be initialized */
            if (!init_state_get_bit(cur, src2)) {
                char buf[256];
                snprintf(buf, sizeof(buf),
                         "PATCH_CLOSURE value register %d may be uninitialized",
                         src2);
                set_error(err, VERR_UNINITIALIZED_VARIABLE,
                          buf, instr_idx, opcode);
                result = MENAI_ERR_UNDEFINED_VARIABLE;
                goto done;
            }

            /* src0 must be known to hold a closure */
            if (cur->closure_map[src0] < 0) {
                char buf[256];
                snprintf(buf, sizeof(buf),
                         "PATCH_CLOSURE target slot %d is not known to hold a closure",
                         src0);
                set_error(err, VERR_INVALID_VARIABLE_ACCESS,
                          buf, instr_idx, opcode);
                result = MENAI_ERR_UNDEFINED_VARIABLE;
                goto done;
            }

            /* src1 (capture index) must be < ncap of the target closure's
             * code object */
            int code_obj_index = cur->closure_map[src0];
            if (code_obj_index >= 0 && code_obj_index < (int)co->nchildren) {
                MenaiCodeObject *target_co = co->children[code_obj_index];
                if (src1 >= (int)target_co->ncap) {
                    char buf[256];
                    snprintf(buf, sizeof(buf),
                             "PATCH_CLOSURE capture_slot %d out of range "
                             "for closure with %zd free variable(s)",
                             src1, target_co->ncap);
                    set_error(err, VERR_INDEX_OUT_OF_BOUNDS,
                              buf, instr_idx, opcode);
                    result = MENAI_ERR_CLOSURE_INDEX_OUT_OF_RANGE;
                    goto done;
                }
            }
        }

        /* Check CALL/TAIL_CALL/APPLY/TAIL_APPLY: func register initialized */
        if (opcode == OP_CALL || opcode == OP_TAIL_CALL ||
            opcode == OP_APPLY || opcode == OP_TAIL_APPLY) {
            if (!init_state_get_bit(cur, src0)) {
                char buf[256];
                snprintf(buf, sizeof(buf),
                         "Function register %d may be uninitialized", src0);
                set_error(err, VERR_UNINITIALIZED_VARIABLE,
                          buf, instr_idx, opcode);
                result = MENAI_ERR_UNDEFINED_VARIABLE;
                goto done;
            }
        }

        /* Check APPLY/TAIL_APPLY: arg_list register initialized */
        if (opcode == OP_APPLY || opcode == OP_TAIL_APPLY) {
            if (!init_state_get_bit(cur, src1)) {
                char buf[256];
                snprintf(buf, sizeof(buf),
                         "APPLY arg_list register %d may be uninitialized",
                         src1);
                set_error(err, VERR_UNINITIALIZED_VARIABLE,
                          buf, instr_idx, opcode);
                result = MENAI_ERR_UNDEFINED_VARIABLE;
                goto done;
            }
        }

        /* Update initialized set after this instruction */
        if (opcode == OP_MAKE_CLOSURE) {
            init_state_set_bit(cur, dest);
            if (dest >= 0 && dest < V_MAX_SLOTS) {
                cur->closure_map[dest] = src0;
            }
        } else if (!is_no_dest_opcode(opcode)) {
            init_state_set_bit(cur, dest);
            if (dest >= 0 && dest < V_MAX_SLOTS) {
                cur->closure_map[dest] = -1;
            }
        }

        /* Re-union with initial_initialized (params/captures survive) */
        init_state_union(cur, &initial);

        /* Propagate to successors */
        int succs_buf[2];
        int *succs = succs_buf;
        int nsuccs;

        if (opcode == OP_SWITCH_INTEGER && src1 >= 0 && src1 < co->njt) {
            const MenaiJumpTable *t = &co->jump_tables[src1];
            succs = malloc(sizeof(int) * (size_t)(t->count + 1));
            if (!succs) {
                result = MENAI_ERR_NOMEM;
                goto done;
            }

            nsuccs = 0;
            for (int j = 0; j < t->count; j++) {
                succs[nsuccs++] = t->targets[j];
            }

            succs[nsuccs++] = t->default_target;

        } else {
            nsuccs = get_successors(instr_idx, opcode, src0, src1, code_len,
                                    succs);
        }

        for (int s = 0; s < nsuccs; s++) {
            int succ_idx = succs[s];
            if (succ_idx < 0 || succ_idx >= code_len) {
                continue;
            }

            InitState *existing = &states[succ_idx];
            if (!visited[succ_idx]) {
                /* First visit: copy current state */
                init_state_copy(existing, cur, total_slots);
                visited[succ_idx] = 1;
                if (!in_worklist[succ_idx]) {
                    worklist[wl_tail++] = succ_idx;
                    in_worklist[succ_idx] = 1;
                }
            } else {
                /* Merge: intersect existing with current */
                InitState merged;
                merged.closure_map = malloc((size_t)total_slots * sizeof(int));
                if (!merged.closure_map) {
                    result = MENAI_ERR_NOMEM;
                    goto done;
                }
                init_state_copy(&merged, existing, total_slots);
                init_state_intersect(&merged, cur, total_slots);

                if (!init_state_equal(&merged, existing, total_slots)) {
                    init_state_copy(existing, &merged, total_slots);
                    if (!in_worklist[succ_idx]) {
                        worklist[wl_tail++] = succ_idx;
                        in_worklist[succ_idx] = 1;
                    }
                }
                free(merged.closure_map);
            }
        }

        if (succs != succs_buf) {
            free(succs);
        }
    }

done:
    for (int i = 0; i < code_len; i++) { free(states[i].closure_map); }
    free(states);
    free(initial.closure_map);
    free(worklist);
    free(in_worklist);
    free(visited);
    return result;
}

/*
 * menai_validate — validate a code object recursively.
 *
 * Returns MENAI_OK (0) if valid, or a negative error code if invalid.
 * On error, fills in out_err with details (if non-NULL).
 */
int
menai_validate(MenaiCodeObject *co, MenaiValidationError *out_err)
{
    if (!co) {
        return MENAI_ERR_VALUE;
    }

    /* Recursively validate all nested code objects first */
    for (ssize_t i = 0; i < co->nchildren; i++) {
        int rc = menai_validate(co->children[i], out_err);
        if (rc != MENAI_OK) {
            return rc;
        }
    }

    /* Validate this code object */
    int rc = validate_structure(co, out_err);
    if (rc != MENAI_OK) {
        return rc;
    }

    rc = validate_indices(co, out_err);
    if (rc != MENAI_OK) {
        return rc;
    }

    rc = validate_control_flow(co, out_err);
    if (rc != MENAI_OK) {
        return rc;
    }

    rc = validate_initialization(co, out_err);
    return rc;
}
