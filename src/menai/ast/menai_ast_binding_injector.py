"""
Menai Binding Injector Pass - splices host-supplied values into a program as a
lexical binding.

This pass wraps a program in a single ``let`` binding whose right-hand side is a
value the host constructed, so the program can read it by name::

    (let ((inputs <host-supplied value>))
      <program>)

The value is carried into the compilation as an ordinary constant via
``MenaiASTConstant``; no source text is generated and no value is ever
converted to a string.

The binding is one layer above the prelude and one layer below the program, so
the program sees both the prelude's functions and the host's binding, while the
host's binding shadows nothing in the prelude.

Injection happens after module resolution.  Module ASTs are inlined into the
parent by the module resolver, so an inlined module body sits inside the
binding's lexical scope and could in principle resolve the binding's name.  In
practice modules are static and do not reference host-chosen names; a host that
wants to be certain should choose a distinctive name.

The binding name is chosen by the host, not fixed by the language, so the
language reserves no identifier for this purpose.  Two names are rejected
because they cannot be ordinary lexical bindings:

- A ``$``-prefixed name is an opcode-backed primitive, resolved before name
  resolution is involved, so it is never a lexical binding.
- A prelude name would shadow the prelude binding and break every prelude call
  in the program.
"""

from menai.ast.menai_ast import MenaiASTConstant, MenaiASTList, MenaiASTNode, MenaiASTSymbol
from menai.ast.menai_ast_prelude_injector import MenaiASTPreludeInjector
from menai.menai_error import MenaiCodegenError
from menai.menai_value import MenaiValue


class MenaiASTBindingInjector:
    """
    Wraps a program in a single lexical binding holding a host-supplied value.
    """

    @classmethod
    def wrap(cls, program: MenaiASTNode, name: str, value: MenaiValue) -> MenaiASTNode:
        """
        Return the program wrapped in ``(let ((name value)) program)``.

        Args:
            program: The program to wrap.
            name: The binding name the program reads the value by.
            value: The host-supplied value.

        Returns:
            The program wrapped in a single ``let`` binding.

        Raises:
            MenaiCodegenError: If the name is a ``$``-prefixed primitive name or
                a prelude name.
        """
        cls._validate_name(name)

        binding = MenaiASTList((MenaiASTSymbol(name), MenaiASTConstant(value)))
        return MenaiASTList((MenaiASTSymbol('let'), MenaiASTList((binding,)), program))

    @classmethod
    def _validate_name(cls, name: str) -> None:
        """
        Reject names that cannot be ordinary lexical bindings.

        Raises:
            MenaiCodegenError: If the name is a ``$``-prefixed primitive name or
                a prelude name.
        """
        if name.startswith('$'):
            raise MenaiCodegenError(
                message=f"Binding name '{name}' uses a reserved prefix",
                context="The '$' prefix is reserved for opcode-backed primitives",
                suggestion="Choose a binding name that does not begin with '$'",
            )

        if name in MenaiASTPreludeInjector.prelude_names():
            raise MenaiCodegenError(
                message=f"Binding name '{name}' collides with a prelude name",
                context="The binding would shadow the prelude binding of the same name",
                suggestion="Choose a binding name that is not a prelude function or constant",
            )
