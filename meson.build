project(
  'pythia',
  license: 'GPL-3.0-or-later',
  meson_version: '>=0.56.0',
)

# Find Python installation
python = import('python').find_installation()

# Set folders
prefix = get_option('prefix')
bindir = get_option('bindir')
pylibdir = python.get_install_dir()
datadir = get_option('datadir')
localedir = get_option('localedir')
mandir = get_option('mandir')
pythiadir = join_paths(pylibdir, 'pythia')
podir = join_paths(meson.source_root(), 'po')

# Generate configuration files
config = configuration_data()
config.set('localedir_path', join_paths(prefix, localedir))

configure_file(
  input: 'optional_settings.py.in',
  output: 'optional_settings.py',
  configuration: config,
  install_dir: pythiadir,
)

# Translations
subdir('po')

# Do installation
install_data(
  files('bin/pythia'),
  install_dir: bindir,
  install_mode: 'rwxr-xr-x',
)

install_subdir(
  'pythia',
  install_dir: pylibdir,
  # A module built in the tree (pip install -e .) is not this build's.
  exclude_files: ['_native.abi3.so', '_native.pyd'],
)

# Pythia's droidtop side, pythia._native (native/), built with cargo.
cargo = find_program('cargo', required: get_option('native'))
if cargo.found()
  custom_target(
    'pythia-native',
    output: '_native.abi3.so',
    command: [python, files('utils/build_native.py'), meson.current_source_dir(), '@OUTPUT@'],
    build_by_default: true,
    build_always_stale: true,
    install: true,
    install_dir: pythiadir,
  )
endif

install_subdir(
  'share/icons',
  install_dir: join_paths(datadir, 'icons'),
  strip_directory: true,
)


install_subdir(
  'share/pythia',
  install_dir: join_paths(datadir, 'pythia'),
  strip_directory: true,
)

install_man(
  files('share/man/man1/pythia.1'),
  install_dir: join_paths(mandir, 'man1'),
)

desktop_file = i18n.merge_file(
  input: files('share/applications/net.pythia.Pythia.desktop'),
  output: 'net.pythia.Pythia.desktop',
  type: 'desktop',
  po_dir: podir,
  install: true,
  install_dir: join_paths(datadir, 'applications'),
)

# Validate the desktop file
desktop_file_validate = find_program('desktop-file-validate', required:false)
if desktop_file_validate.found()
  test (
    'Validate desktop file',
    desktop_file_validate,
    args: [ desktop_file.full_path() ],
  )
endif

desktop_file_association = i18n.merge_file(
  input: files('share/applications/net.pythia.Pythia1.desktop'),
  output: 'net.pythia.Pythia1.desktop',
  type: 'desktop',
  po_dir: podir,
  install: true,
  install_dir: join_paths(datadir, 'applications'),
)

# Validate the desktop file
desktop_file_association_validate = find_program('desktop-file-validate', required:false)
if desktop_file_association_validate.found()
  test (
    'Validate desktop file',
    desktop_file_association_validate,
    args: [ desktop_file_association.full_path() ],
  )
endif

metainfo_file = i18n.merge_file(
  input: files('share/metainfo/net.pythia.Pythia.metainfo.xml'),
  output: 'net.pythia.Pythia.metainfo.xml',
  type: 'xml',
  po_dir: podir,
  install: true,
  install_dir: join_paths(datadir, 'metainfo'),
)

# Validate the metainfo file
appstreamcli = find_program('appstream-util', required: false)
if appstreamcli.found()
  test (
    'Validate metainfo file',
    appstreamcli,
    args: ['validate-relax', '--nonet', metainfo_file.full_path() ]
  )
endif

meson.add_install_script('utils/meson_post_install.py')
