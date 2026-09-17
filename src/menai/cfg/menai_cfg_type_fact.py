"""
Type facts for CFG interprocedural type analysis.

A TypeFact records what is statically known about the type of an SSA value.
The lattice has three levels:

    BOTTOM          no information yet (the initial value)
    Known(kind, struct_type)   a proven type
    ANY             conflicting proven types (top)

BOTTOM is the bottom element and ANY is the top element.  Keeping "no
information" and "conflicting information" distinct is what makes the
interprocedural fixed point converge: joining two different known kinds yields
ANY (above both), not BOTTOM, so parameter facts only ever move up the lattice.

The struct type is carried only when the kind is 'struct' and the specific
struct type identity is known.  MenaiStructType equality and hashing are
tag-based, so facts compare and join by struct type identity.

Join rules:

    BOTTOM  ⊔ x              = x
    ANY     ⊔ x              = ANY
    Known(k) ⊔ Known(k)      = Known(k)                (k != 'struct')
    Known('struct', t) ⊔ Known('struct', t) = Known('struct', t)
    Known('struct', t1) ⊔ Known('struct', t2) = Known('struct', None)  (t1 != t2)
    Known(k1) ⊔ Known(k2)    = ANY                     (k1 != k2)

A join of two different struct types yields Known('struct', None): the value is
still known to be a struct, but its specific type identity is lost, so field
accesses cannot be resolved to indices.
"""

from dataclasses import dataclass

from menai.menai_value import (
    MenaiBoolean,
    MenaiBytes,
    MenaiComplex,
    MenaiDict,
    MenaiFloat,
    MenaiFunction,
    MenaiInteger,
    MenaiList,
    MenaiNone,
    MenaiSet,
    MenaiString,
    MenaiStruct,
    MenaiStructType,
    MenaiSymbol,
    MenaiVector,
)

_VALUE_TYPE_MAP = {
    MenaiNone: 'none',
    MenaiBoolean: 'boolean',
    MenaiInteger: 'integer',
    MenaiFloat: 'float',
    MenaiComplex: 'complex',
    MenaiString: 'string',
    MenaiSymbol: 'symbol',
    MenaiList: 'list',
    MenaiDict: 'dict',
    MenaiSet: 'set',
    MenaiVector: 'vector',
    MenaiFunction: 'function',
    MenaiBytes: 'bytes',
    MenaiStruct: 'struct',
    MenaiStructType: 'structtype',
}


@dataclass(frozen=True)
class TypeFact:
    """
    What is statically known about the type of an SSA value.

    `kind` is one of:
      - 'bottom' — no information yet
      - a Menai type name — a proven type
      - 'any' — conflicting proven types (top)

    `struct_type` is set only when `kind == 'struct'` and the specific struct
    type identity is known; it is None when the value is a struct of unknown
    identity.
    """
    kind: str
    struct_type: MenaiStructType | None = None

    def is_bottom(self) -> bool:
        """True if no type information is known yet."""
        return self.kind == 'bottom'

    def is_any(self) -> bool:
        """True if the value has conflicting proven types."""
        return self.kind == 'any'

    def is_known(self) -> bool:
        """True if a single type is proven."""
        return self.kind not in ('bottom', 'any')

    def is_struct_of(self, struct_type: MenaiStructType) -> bool:
        """True if this fact proves the value is a struct of the given type."""
        return self.kind == 'struct' and self.struct_type == struct_type


BOTTOM = TypeFact(kind='bottom')
ANY = TypeFact(kind='any')


def fact_for_value(value: object) -> TypeFact:
    """
    Return the type fact for a constant MenaiValue instance.

    A MenaiStruct carries its struct type; every other value maps to its kind
    with no struct type.  Values of unrecognised classes yield BOTTOM.
    """
    if isinstance(value, MenaiStruct):
        return TypeFact(kind='struct', struct_type=value.struct_type)

    for cls, name in _VALUE_TYPE_MAP.items():
        if isinstance(value, cls):
            return TypeFact(kind=name)

    return BOTTOM


def join(a: TypeFact, b: TypeFact) -> TypeFact:
    """
    Least upper bound of two type facts.

    Returns the most precise fact implied by both inputs.  See the module
    docstring for the join rules.
    """
    if a.is_bottom():
        return b

    if b.is_bottom():
        return a

    if a.is_any() or b.is_any():
        return ANY

    if a.kind != b.kind:
        return ANY

    if a.kind == 'struct' and a.struct_type != b.struct_type:
        return TypeFact(kind='struct')

    return a
