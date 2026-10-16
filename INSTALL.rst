Installing Pythia
=================

Requirements
------------

Pythia should work on any up to date Linux system. It is based on Python and
Gtk but will run on any desktop environment.
If you installed Pythia from our PPA or some other repository, it should already
come with all of its essential dependencies. However, if you need to install
Pythia manually, it requires the following components:

    * Python >= 3.10
    * PyGObject
    * PyGObject bindings for: Gtk, Gdk, Cairo, GnomeDesktop, Webkit2, Notify
    * python3-requests
    * python3-pillow
    * python3-yaml
    * python3-setproctitle
    * python3-distro
    * python3-evdev (optional, for controller detection)

Building Pythia (``pip install .``, meson or the Debian package) also builds
its droidtop side, ``pythia._native`` (``native/``), so it needs a Rust
toolchain, 1.85 or newer, with cargo. On Ubuntu 24.04 that is the
``rustc-1.85`` and ``cargo-1.85`` packages. On Linux it also needs libudev's
headers and pkg-config (``libudev-dev`` and ``pkg-config``), for reading game
controllers in the handheld shell. Cargo fetches droidtop-agent's crates from
GitHub, pinned by commit in ``native/Cargo.toml``.

These dependencies are only for running the Pythia client. To install and run
games themselves we recommend you install the following packages:

  * psmisc (or the package providing 'fuser')
  * p7zip (or the package providing '7z')
  * curl
  * fluid-soundfont-gs (or other soundfonts for MIDI support)
  * cabextract (if needed, to install Windows games)
  * x11-xserver-utils (or the package providing 'xrandr', if you are running
    Xorg, if you are not, you will depend on the GnomeDesktop bindings to fetch
    screen resolutions on Wayland, the GnomeDesktop library is not directly
    related to the Gnome desktop and is only used as a xrandr replacement.)
  * The 32bit OpenGL and Vulkan drivers for your graphics card
  * Wine (not actually needed, but installing it is the easiest way to get all
    the libraries missing from our runtime).

To install all those dependencies (except for Wine and graphics drivers)
on Ubuntu based systems, you can run::

    sudo apt install python3-yaml python3-requests python3-pil python3-gi python3-gi-cairo \
      gir1.2-gtk-3.0 gir1.2-gnomedesktop-3.0 gir1.2-webkit2-4.0 \
      gir1.2-notify-0.7 psmisc cabextract unzip p7zip curl fluid-soundfont-gs \
      x11-xserver-utils python3-evdev libgirepository1.0-dev \
      python3-setproctitle python3-distro

Note :
If you use OpenSUSE, some dependencies are missing. You need to install python3-gobject-Gdk and typelib-1_0-Gtk-3_0

``sudo zypper install python3-gobject-Gdk typelib-1_0-Gtk-3_0``

Installation
------------

Pythia is not packaged or distributed anywhere -- it's a personal project you
run from source::

    git clone https://github.com/bi0shacker001/pythia
    cd pythia
    git checkout lutris-core
    ./bin/pythia

Run Pythia
-----------

If you installed Pythia using a package, you can launch the program by typing
``pythia`` at the command line. And if you want to run Pythia from the source tree,
type ``./bin/pythia``
