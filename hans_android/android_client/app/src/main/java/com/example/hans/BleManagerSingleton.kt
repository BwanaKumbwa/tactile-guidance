package com.example.hans

import android.content.Context

/**
 * Global singleton for Bluetooth device managers.
 * Ensures only one connection per device, shared across all activities.
 */
object BleManagerSingleton {
    private var braceletManager: BleManager? = null
    private var beltManager: BleManager? = null

    fun getBraceletManager(context: Context): BleManager {
        if (braceletManager == null) {
            braceletManager = BleManager(context)
        }
        return braceletManager!!
    }

    fun getBeltManager(context: Context): BleManager {
        if (beltManager == null) {
            beltManager = BleManager(context)
        }
        return beltManager!!
    }

    fun disconnectAll() {
        braceletManager?.disconnect()
        beltManager?.disconnect()
    }
}