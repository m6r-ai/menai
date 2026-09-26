"""
Regression tests for outgoing-zone back-propagation of registers that are also
read as persistent state.

The slot allocator's Phase 3 back-propagates a call argument's register into the
outgoing zone (slots local_count..local_count+N-1) when the call is the
register's last use.  The outgoing zone is transient argument-staging space, so
a register that is *also* read in a position the bytecode validator requires to
be a local slot must not be moved there.  Such positions are:

  - JUMP_IF_TRUE / JUMP_IF_FALSE condition
  - SWITCH scrutinee
  - CALL / TAIL_CALL function register
  - APPLY / TAIL_APPLY function register and arg-list register
  - RAISE_ERROR message register

A register used in one of those positions and later as a call argument used to
be back-propagated into the outgoing zone, producing bytecode the validator
rejects ("Condition register N out of bounds (local_count: M)").

The module below is the body of the standard-library `bmp-encode` module, which
is where the fault was found.  Its `encode` binds `bottom-up` with a `let`, reads
it as an `if` condition, and passes it as the final argument of `encode-rows`,
whose result is used.  The call is the binding's last use, which is exactly the
shape that triggered the fault.
"""

from menai import Menai
from menai.bytecode.menai_bytecode import Opcode, unpack_instruction

_MODULE_SOURCE = """
(letrec
  ((signature (string->bytes "BM"))
   (dib-header-size 40)
   (data-offset 54)
   (row-stride
    (lambda (width bpp)
      (integer* (integer/ (integer+ (integer* width bpp) 31) 32) 4)))
   (row-padding
    (lambda (width bpp)
      (integer- (row-stride width bpp) (integer/ (integer* width bpp) 8))))
   (encode-pixel
    (lambda (b pixel bpp)
      (let ((channels (if (vector? pixel) (vector->list pixel) #none)))
        (if (none? channels)
            (error "each pixel must be a vector of channels")
            (if (integer=? bpp 24)
                (bytes-append-u8
                  (bytes-append-u8
                    (bytes-append-u8 b (list-ref channels 2))
                    (list-ref channels 1))
                  (list-ref channels 0))
                (bytes-append-u8
                  (bytes-append-u8
                    (bytes-append-u8
                      (bytes-append-u8 b (list-ref channels 2))
                      (list-ref channels 1))
                    (list-ref channels 0))
                  (list-ref channels 3)))))))
   (encode-row
    (lambda (b row width bpp)
      (if (vector? row)
          (letrec ((loop (lambda (x acc)
                           (if (integer>=? x width)
                               acc
                               (loop (integer+ x 1)
                                     (encode-pixel acc (vector-ref row x) bpp))))))
            (let ((padded (loop 0 b)))
              (letrec ((pad (lambda (n acc)
                              (if (integer>=? n (row-padding width bpp))
                                  acc
                                  (pad (integer+ n 1) (bytes-append-u8 acc 0))))))
                (pad 0 padded))))
          (error "each row must be a vector of pixels"))))
   (encode-rows
    (lambda (b pixels width height bpp bottom-up)
      (if (vector? pixels)
          (let ((ordered (if bottom-up (list->vector (list-reverse (vector->list pixels))) pixels)))
            (letrec ((loop (lambda (y acc)
                             (if (integer>=? y height)
                                 acc
                                 (loop (integer+ y 1)
                                       (encode-row acc (vector-ref ordered y) width bpp))))))
              (loop 0 b)))
          (error "pixels must be a vector of rows"))))
   (encode-header
    (lambda (header width height bpp image-size)
      (bytes-concat
        signature
        (bytes-append-u32-le (bytes-concat) (integer+ data-offset image-size))
        (bytes-append-u16-le (bytes-concat) 0)
        (bytes-append-u16-le (bytes-concat) 0)
        (bytes-append-u32-le (bytes-concat) data-offset)
        (bytes-append-u32-le (bytes-concat) dib-header-size)
        (bytes-append-u32-le (bytes-concat) width)
        (bytes-append-i32-le (bytes-concat) height)
        (bytes-append-u16-le (bytes-concat) (dict-get header "planes"))
        (bytes-append-u16-le (bytes-concat) bpp)
        (bytes-append-u32-le (bytes-concat) 0)
        (bytes-append-u32-le (bytes-concat) image-size)
        (bytes-append-i32-le (bytes-concat) (dict-get header "x-ppm"))
        (bytes-append-i32-le (bytes-concat) (dict-get header "y-ppm"))
        (bytes-append-u32-le (bytes-concat) (dict-get header "colours-used"))
        (bytes-append-u32-le (bytes-concat) (dict-get header "colours-important")))))
   (encode
    (lambda (container)
      (if (dict? container)
          (let ((header (dict-get container "header"))
                (pixels (dict-get container "pixels"))
                (meta (dict-get container "meta")))
            (if (dict? header)
                (let ((width (dict-get header "width"))
                      (bpp (dict-get header "bpp")))
                  (if (or (integer=? bpp 24) (integer=? bpp 32))
                      (let ((bottom-up (if (dict? meta) (dict-get meta "bottom-up") #t))
                            (abs-height (integer-abs (dict-get header "height"))))
                        (let ((file-height (if bottom-up
                                               abs-height
                                               (integer-neg abs-height))))
                          (let ((image-size (integer* (row-stride width bpp) abs-height)))
                            (let ((data (encode-rows (bytes-concat) pixels width abs-height
                                                     bpp bottom-up)))
                              (bytes-concat
                                (encode-header header width file-height bpp image-size)
                                data)))))
                      (error "unsupported bit depth")))
                (error "header must be a dict")))
          (error "encode expects a dict")))))
  (export encode))
"""

_CALL_SITE = '(let ((p (import "regression_outgoing_zone"))) (:: p encode))'

_CONTAINER = (
    '(dict "header" (dict "width" 1 "height" 1 "bpp" 24 "planes" 1'
    ' "x-ppm" 2835 "y-ppm" 2835 "colours-used" 0 "colours-important" 0)'
    ' "pixels" (vector (vector (vector 10 20 30)))'
    ' "meta" (dict "bottom-up" #t))'
)


def _write_module(tmp_path):
    """Write the regression module into tmp_path and return that directory."""
    module_file = tmp_path / "regression_outgoing_zone.menai"
    module_file.write_text(_MODULE_SOURCE)
    return tmp_path


def _walk_branches(code):
    """Yield (code_object, instruction_index, condition_register) for every branch."""
    for index, instruction in enumerate(code.instructions):
        unpacked = unpack_instruction(instruction)
        if int(unpacked.opcode) in (
            int(Opcode.JUMP_IF_TRUE),
            int(Opcode.JUMP_IF_FALSE),
            int(Opcode.SWITCH_INTEGER),
        ):
            yield code, index, unpacked.src0

    for nested in code.code_objects:
        yield from _walk_branches(nested)


class TestOutgoingZoneLocalRequiredUses:
    """A register read as persistent state must not be moved to the outgoing zone."""

    def test_module_compiles_and_validates(self, tmp_path):
        """
        The module compiles and its bytecode passes validation.

        Before the fix, the branch condition register was back-propagated into
        the outgoing zone and the validator rejected the bytecode.
        """
        _write_module(tmp_path)
        menai = Menai(module_path=[str(tmp_path)])

        result = menai.evaluate(_CALL_SITE)

        assert result is not None

    def test_branch_conditions_are_local_slots(self, tmp_path):
        """
        Every branch condition register lies inside the local region.

        The outgoing zone begins at local_count, so a condition register must
        be strictly less than local_count.
        """
        _write_module(tmp_path)
        menai = Menai(module_path=[str(tmp_path)])

        code = menai.compile(_CALL_SITE)

        checked = 0
        for code_object, index, condition_register in _walk_branches(code):
            assert condition_register < code_object.local_count, (
                f"{code_object.name} instruction {index}: condition register "
                f"{condition_register} is not below local_count "
                f"{code_object.local_count}"
            )
            checked += 1

        assert checked > 0, "expected at least one branch in the compiled program"

    def test_module_evaluates_correctly(self, tmp_path):
        """The encoded output is correct, confirming the fix preserves behaviour."""
        _write_module(tmp_path)
        menai = Menai(module_path=[str(tmp_path)])

        result = menai.evaluate(
            f'(let ((p (import "regression_outgoing_zone")))'
            f' ((:: p encode) {_CONTAINER}))'
        )

        # BITMAPFILEHEADER: "BM", file-size, reserved, reserved, data-offset.
        assert result[:2] == b"BM"
        # data-offset is 54; width 1; height 1; bpp 24.
        assert result[10:14] == (54).to_bytes(4, "little")
        assert result[18:22] == (1).to_bytes(4, "little")
        assert result[22:26] == (1).to_bytes(4, "little")
        assert result[28:30] == (24).to_bytes(2, "little")
