package com.example.hans

import android.app.Application
import android.util.Log

class HansApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        
        Log.d("HANS", "🚀 App process created")
        
        // Only warmup ARCore if it's available (withArcore flavor)
        try {
            ArCoreManager.warmupAsync(this)
            Log.d("HANS", "ARCore warmup triggered")
        } catch (e: Exception) {
            Log.d("HANS", "ARCore not available (noArcore flavor) — skipping warmup")
        }
    }
}