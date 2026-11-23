package dev.mytop.pythia.ui

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class LocationsTest {
    private fun linux(vararg env: Pair<String, String>) = Locations(false, mapOf(*env)::get)

    private fun windows(vararg env: Pair<String, String>) = Locations(true, mapOf(*env)::get)

    @Test
    fun theDataFolderOverrideWinsAndPutsTheSocketUnderRun() {
        val here = linux("PYTHIA_DATA_DIR" to "/tmp/p", "XDG_RUNTIME_DIR" to "/run/user/1000")
        assertEquals("/tmp/p", here.dataDir())
        assertEquals("/tmp/p/instance.key", here.keyPath())
        assertEquals("/tmp/p/run/backend.sock", here.address())
    }

    @Test
    fun theRuntimeFolderOfTheSessionIsUsedWhenThereIsNoOverride() {
        val here = linux("XDG_RUNTIME_DIR" to "/run/user/1000", "HOME" to "/home/a")
        assertEquals("/run/user/1000/pythia/backend.sock", here.address())
        assertEquals("/home/a/.local/share/pythia/instance.key", here.keyPath())
    }

    @Test
    fun withNeitherTheSocketIsUnderTheDataFolder() {
        val here = linux("HOME" to "/home/a", "XDG_DATA_HOME" to "/data")
        assertEquals("/data/pythia/run/backend.sock", here.address())
    }

    @Test
    fun theExplicitRuntimeFolderIsUsedFirst() {
        assertEquals("/x/backend.sock", linux("PYTHIA_RUNTIME_DIR" to "/x", "PYTHIA_DATA_DIR" to "/d").address())
    }

    @Test
    fun onWindowsTheChannelIsAPipeNamedForTheCleanedUserName() {
        val here = windows("USERNAME" to "a b\c", "APPDATA" to "C:\Users\a\AppData\Roaming")
        assertEquals("\\.\pipe\pythia-backend-a_b_c", here.address())
        assertEquals("C:\Users\a\AppData\Roaming\Pythia\instance.key", here.keyPath())
    }

    @Test
    fun aKeyIsHex() {
        assertEquals("00ff10", parseKey(" 00FF10\n"))
        assertNull(parseKey(""))
        assertNull(parseKey("abc"))
        assertNull(parseKey("zz"))
    }
}
