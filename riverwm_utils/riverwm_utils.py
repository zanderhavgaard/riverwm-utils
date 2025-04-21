"""Utilities for river wayland compositor"""

import sys
import os
import argparse
import struct


def ensure_river_bindings(cache_dir: str) -> None:
    """
    Try importing the river bindings; if missing, generate them into `cache_dir`.
    Exits on failure.
    """
    # make sure cache is in sys.path before any import
    if cache_dir not in sys.path:
        sys.path.insert(0, cache_dir)

    try:
        import pywayland
        import pywayland.protocol.river_control_unstable_v1  # noqa: F401
        import pywayland.protocol.river_status_unstable_v1  # noqa: F401
        import pywayland.protocol.wayland  # noqa: #F401

        return
    except ModuleNotFoundError:
        print("River bindings missing, generating into", cache_dir)
        try:
            _generate_river_wayland_protocol_files(cache_dir)
            print("Bindings generated—please rerun the command.")
            sys.exit(0)
        except Exception as e:
            sys.exit(f"Failed to generate bindings: {e!r}")


def _generate_river_wayland_protocol_files(cache_dir: str) -> None:
    from pywayland.scanner.protocol import Protocol
    import pywayland

    xml_dir = os.path.dirname(__file__)
    inputs = (
        "wayland.xml",
        "river-control-unstable-v1.xml",
        "river-status-unstable-v1.xml",
    )
    protocols = [Protocol.parse_file(os.path.join(xml_dir, f)) for f in inputs]

    # create package dirs
    base_pkg = os.path.join(cache_dir, "pywayland")
    out_pkg = os.path.join(base_pkg, "protocol")
    os.makedirs(out_pkg, exist_ok=True)
    for pkg in (base_pkg, out_pkg):
        init_py = os.path.join(pkg, "__init__.py")
        if not os.path.exists(init_py):
            open(init_py, "w").close()

    # now that sys.path[0] == cache_dir, imports will see them
    if cache_dir not in sys.path:
        sys.path.insert(0, cache_dir)

    # write out each protocol
    imports = {
        interface.name: protocol.name
        for protocol in protocols
        for interface in protocol.interface
    }
    for proto in protocols:
        proto.output(out_pkg, imports)


# def _generate_river_wayland_protocol_files(cache_dir: str) -> None:
#     from pywayland.scanner.protocol import Protocol
#     import pywayland
#
#     xml_dir = os.path.dirname(__file__)
#     inputs = (
#         "wayland.xml",
#         "river-control-unstable-v1.xml",
#         "river-status-unstable-v1.xml",
#     )
#     protocols = [Protocol.parse_file(os.path.join(xml_dir, f)) for f in inputs]
#
#     # make sure protocols directory is created
#     generated_protocol_output_dir = os.path.join(cache_dir, "pywayland", "protocol")
#     os.makedirs(generated_protocol_output_dir, exist_ok=True)
#
#     # add init file to make generated protcol files importable
#     init_py = os.path.join(generated_protocol_output_dir, "__init__.py")
#     if not os.path.exists(init_py):
#         open(init_py, "w").close()
#
#     # make Python find it
#     if cache_dir not in sys.path:
#         sys.path.insert(0, cache_dir)
#
#     # write out each protocol
#     imports = {
#         interface.name: protocol.name
#         for protocol in protocols
#         for interface in protocol.interface
#     }
#     for proto in protocols:
#         proto.output(generated_protocol_output_dir, imports)


CACHE_DIR_PATH = os.path.expanduser(
    os.environ.get("XDG_CACHE_HOME", "~/.cache/riverwm-utils")
)

ensure_river_bindings(CACHE_DIR_PATH)

breakpoint()


# now imports succeed and you can pull in all the classes safely:
from pywayland.protocol.wayland import WlOutput, WlSeat, WlRegistry
from pywayland.protocol.river_control_unstable_v1 import ZriverControlV1
from pywayland.protocol.river_status_unstable_v1 import ZriverStatusManagerV1

from pywayland.client import Display  # pylint: disable=import-error

breakpoint()


STATUS_MANAGER = None
CONTROL = None

OUTPUTS = []
SEAT = None


class Output:
    """Represents a wayland output a.k.a. a display"""

    def __init__(self):
        self.wl_output = None
        self.focused_tags = None
        self.view_tags = None
        self.tags = None
        self.status = None

    def destroy(self) -> None:
        """Cleanup"""
        if self.wl_output is not None:
            self.wl_output.destroy()
        if self.status is not None:
            self.status.destroy()

    def configure(self) -> None:
        """Setup"""
        self.status = STATUS_MANAGER.get_river_output_status(self.wl_output)
        self.status.user_data = self
        self.status.dispatcher["focused_tags"] = self.handle_focused_tags
        self.status.dispatcher["view_tags"] = self.handle_view_tags

    def handle_focused_tags(self, _, tags: int) -> None:
        """Handle Event"""
        self.focused_tags = tags

    def handle_view_tags(self, _, tags: int) -> None:
        """Handle Event"""
        self.view_tags = tags


class Seat:
    """Represents a wayland seat"""

    def __init__(self):
        self.wl_seat = None
        self.status = None
        self.focused_output = None

    def destroy(self) -> None:
        """Cleanup"""
        if self.wl_seat is not None:
            self.wl_seat.destroy()

        if self.status is not None:
            self.status.destroy()

    def configure(self) -> None:
        """Setup"""
        self.status = STATUS_MANAGER.get_river_seat_status(self.wl_seat)
        self.status.user_data = self
        self.status.dispatcher["focused_output"] = self.handle_focused_output

    def handle_focused_output(self, _, wl_output: WlOutput) -> None:
        """Handle Event"""
        for output in OUTPUTS:
            if output.wl_output == wl_output:
                self.focused_output = output


def registry_handle_global(
    registry: WlRegistry, wid: int, interface: str, version: int
) -> None:
    """Main Event Handler"""
    global STATUS_MANAGER
    global CONTROL
    global SEAT

    if interface == "zriver_status_manager_v1":
        STATUS_MANAGER = registry.bind(wid, ZriverStatusManagerV1, version)
    elif interface == "zriver_control_v1":
        CONTROL = registry.bind(wid, ZriverControlV1, version)
    elif interface == "wl_output":
        output = Output()
        output.wl_output = registry.bind(wid, WlOutput, version)
        OUTPUTS.append(output)
    elif interface == "wl_seat":
        # We only care about the first seat
        if SEAT is None:
            SEAT = Seat()
            SEAT.wl_seat = registry.bind(wid, WlSeat, version)


def prepare_display(display: Display) -> None:
    """Prepare display global objects"""
    display.connect()

    registry = display.get_registry()
    registry.dispatcher["global"] = registry_handle_global

    display.dispatch(block=True)
    display.roundtrip()

    if STATUS_MANAGER is None:
        print("Failed to bind river status manager")
        sys.exit()

    if CONTROL is None:
        print("Failed to bind river control")
        sys.exit()

    # Configuring all outputs, even the ones we do not care about,
    # should be faster than first waiting for river to advertise the
    # focused output of the SEAT.
    for output in OUTPUTS:
        output.configure()

    SEAT.configure()

    display.dispatch(block=True)
    display.roundtrip()


def close_display(display: Display) -> None:
    """Clean up objects"""
    SEAT.destroy()
    for output in OUTPUTS:
        output.destroy()

    if STATUS_MANAGER is not None:
        STATUS_MANAGER.destroy()

    if CONTROL is not None:
        CONTROL.destroy()

    display.disconnect()


def check_n_tags(n_tags: int) -> int:
    """Check max tag number argument"""
    error_string = f"Invalid max number of tags: {n_tags}"
    try:
        i_n_tags = int(n_tags)
    except Exception as exc:
        raise argparse.ArgumentTypeError(error_string) from exc

    if i_n_tags < 1 or 32 < i_n_tags:
        raise argparse.ArgumentTypeError(error_string)

    return i_n_tags


def parse_command_line() -> argparse.Namespace:
    """Read commandline arguments"""
    parser = argparse.ArgumentParser(
        description="Change to either the next or previous tags.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "n_cycle",
        default=1,
        nargs="?",
        type=int,
        help=("Number of tags to cycle through. Signed integer."),
    )
    parser.add_argument(
        "n_tags",
        default=32,
        nargs="?",
        type=check_n_tags,
        help=(
            "The tag number the cycling should loop back to the first tag "
            "or to the last tag from the first tag. Integer between 1 and "
            "32 inclusive."
        ),
    )
    parser.add_argument(
        "--all-outputs",
        "-a",
        dest="all_outputs",
        action="store_true",
        help="Cycle the tags for all outputs (following the active output).",
    )
    parser.add_argument(
        "--follow",
        "-f",
        dest="follow",
        action="store_true",
        help="Move the active window when cycling.",
    )
    parser.add_argument(
        "--skip-occupied", "-o", action="store_true", help="Skip occupied tags."
    )
    parser.add_argument(
        "--skip-empty", "-s", action="store_true", help="Skip empty tags."
    )
    parser.add_argument(
        "--debug", "-d", action="store_true", help="Enable debugging output."
    )
    return parser.parse_args()


def get_occupied_tags(cli_args: argparse.Namespace) -> int:
    """Return bitmap of occupied tags as int"""
    used_tags = (1 << cli_args.n_tags) - 1

    if not cli_args.all_outputs or len(OUTPUTS) == 1:
        return get_occupied_from_view_tags(SEAT.focused_output.view_tags) & used_tags

    occupied_tags = 0
    for output in OUTPUTS:
        occupied_tags |= get_occupied_from_view_tags(output.view_tags)

    return occupied_tags & used_tags


def get_occupied_from_view_tags(view_tags: int) -> int:
    """Return bitmap of view_tags occupied tags as int"""
    occupied_tags = 0
    nviews = int(len(view_tags) / 4)
    for view in struct.unpack(f"{nviews}I", view_tags):
        occupied_tags |= view

    return occupied_tags


def get_new_tags(cli_args: argparse.Namespace, occupied_tags: int) -> int:
    """Return the new tag set"""
    used_tags = (1 << cli_args.n_tags) - 1
    tags = SEAT.focused_output.focused_tags & used_tags

    if (
        cli_args.n_cycle == 0  # noqa: W504
        or cli_args.skip_empty
        and occupied_tags == 0
        # All tags empty & we want to skip empty tags
        or cli_args.skip_occupied
        and used_tags == (used_tags ^ occupied_tags)
        # All tags occupied & we want to skip occupied tags
    ):
        return tags

    i = 0
    initial_tags = tags
    last_tag = 1 << (cli_args.n_tags - 1)
    for _ in range(cli_args.n_tags):
        new_tags = 0
        if cli_args.n_cycle > 0:
            # If last tag is set => unset it and set first bit on new_tags
            if (tags & last_tag) != 0:
                tags ^= last_tag
                new_tags = 1

            new_tags |= tags << 1

        else:
            # If lowest bit is set (first tag) => unset it and set
            # last_tag bit on new tags
            if (tags & 1) != 0:
                tags ^= 1
                new_tags = last_tag

            new_tags |= tags >> 1

        tags = new_tags

        if cli_args.skip_empty and not bool(tags & occupied_tags):
            continue

        if cli_args.skip_occupied and bool(tags & occupied_tags):
            continue

        i += 1

        if i == abs(cli_args.n_cycle) % cli_args.n_tags:
            return tags

    # Looped over all tags without returning, either skip options caused
    # none of the potential tags to be viable or something went wrong.
    if cli_args.skip_empty:
        print("Cycle failed: all tags empty")
    elif cli_args.skip_occupied:
        print("Cycle failed: all tags occupied")
    else:
        # Something is wrong, bail out.
        print("Cycle failed: looped over all tags")

    return initial_tags


def set_new_tags(cli_args: argparse.Namespace, new_tags: int) -> None:
    """Set the focused tags"""
    if cli_args.follow:
        CONTROL.add_argument("set-view-tags")
        CONTROL.add_argument(str(new_tags))
        CONTROL.run_command(SEAT.wl_seat)

    CONTROL.add_argument("set-focused-tags")
    CONTROL.add_argument(str(new_tags))
    CONTROL.run_command(SEAT.wl_seat)

    if len(OUTPUTS) == 1 or not cli_args.all_outputs:
        return

    # The active output has been switched, walk over all other outputs and
    # set their tags too, wrapping back to the start (where setting can be
    # skipped).
    for i in range(len(OUTPUTS)):
        CONTROL.add_argument("focus-output")
        CONTROL.add_argument("next")
        CONTROL.run_command(SEAT.wl_seat)

        if i + 1 == len(OUTPUTS):
            # Back to the start which has already had it's tags set.
            # Breaking here isn't needed but the next assignment is
            # redundant.
            break

        CONTROL.add_argument("set-focused-tags")
        CONTROL.add_argument(str(new_tags))
        CONTROL.run_command(SEAT.wl_seat)

    return


def cycle_focused_tags() -> None:
    """Shift to next or previous tags"""
    args = parse_command_line()
    display = Display()
    prepare_display(display)

    occupied_tags = get_occupied_tags(args)
    new_tags = get_new_tags(args, occupied_tags)

    if args.debug:
        print(f"cur 0b{SEAT.focused_output.focused_tags:032b}")
        print(f"occ 0b{occupied_tags:032b}")
        print(f"new 0b{new_tags:032b}")

    set_new_tags(args, new_tags)

    display.dispatch(block=True)
    display.roundtrip()

    close_display(display)
