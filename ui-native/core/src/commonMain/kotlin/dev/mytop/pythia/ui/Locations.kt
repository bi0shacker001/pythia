package dev.mytop.pythia.ui

/**
 * Where Pythia's backend channel is and where its key lives, found the way Pythia finds them
 * (pythia/identity.py app_data_dir, pythia/ipc/transport.py address and runtime_dir, pythia/instance.py key_path),
 * so this program and the Pythia it talks to agree with no configuration. [env] reads an environment variable.
 */
class Locations(private val isWindows: Boolean, private val env: (String) -> String?) {
    private fun set(name: String): String? = env(name)?.takeIf { it.isNotEmpty() }

    private fun join(base: String, vararg parts: String): String {
        val sep = if (isWindows) "\\" else "/"
        return parts.fold(base.trimEnd('/', '\\')) { acc, part -> acc + sep + part }
    }

    /** The user name as Pythia cleans it for a pipe name (getpass.getuser, then every odd character to an underscore). */
    fun user(): String {
        val raw = listOf("LOGNAME", "USER", "LNAME", "USERNAME").firstNotNullOfOrNull { set(it) } ?: "user"
        return raw.map { if (it in 'A'..'Z' || it in 'a'..'z' || it in '0'..'9' || it == '_' || it == '.' || it == '-') it else '_' }
            .joinToString("")
    }

    /** Pythia's data folder. */
    fun dataDir(): String {
        set("PYTHIA_DATA_DIR")?.let { return it }
        if (isWindows) return join(set("APPDATA") ?: home(), "Pythia")
        return join(set("XDG_DATA_HOME") ?: join(home(), ".local", "share"), "pythia")
    }

    private fun home(): String = (if (isWindows) set("USERPROFILE") else set("HOME")) ?: "."

    /** The file holding the install's key, as hex. */
    fun keyPath(): String = join(dataDir(), "instance.key")

    /** The channel: a pipe name on Windows, a socket path elsewhere. */
    fun address(): String {
        if (isWindows) return "\\\\.\\pipe\\pythia-backend-" + user()
        set("PYTHIA_RUNTIME_DIR")?.let { return join(it, "backend.sock") }
        if (set("PYTHIA_DATA_DIR") != null) return join(dataDir(), "run", "backend.sock")
        set("XDG_RUNTIME_DIR")?.let { return join(it, "pythia", "backend.sock") }
        return join(dataDir(), "run", "backend.sock")
    }
}

/** The key's bytes as the hex the `hello` message carries; null when the text is not a key. */
fun parseKey(text: String): String? {
    val hex = text.trim().lowercase()
    return hex.takeIf { it.isNotEmpty() && it.length % 2 == 0 && it.all { c -> c in '0'..'9' || c in 'a'..'f' } }
}
