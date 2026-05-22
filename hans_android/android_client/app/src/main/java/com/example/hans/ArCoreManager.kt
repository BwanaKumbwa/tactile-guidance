package com.example.hans

import android.content.Context
import android.util.Log

object ArCoreManager {
    @Volatile
    private var arSession: Any? = null
    
    @Volatile
    private var isInitializing = false
    
    @Volatile
    private var isReady = false
    
    fun getSession(): Any? = arSession
    fun isReady(): Boolean = isReady
    
    fun warmupAsync(context: Context) {
        if (isInitializing || isReady || arSession != null) return
        
        // Only run on withArcore flavor
        if (!hasArCoreLibrary()) {
            Log.d("HANS", "ARCore library not available (noArcore flavor)")
            return
        }
        
        isInitializing = true
        Thread {
            try {
                Log.d("HANS", "🔥 ARCore warmup started...")
                initializeArCoreIfAvailable(context)
                isReady = true
            } catch (e: Exception) {
                Log.w("HANS", "ARCore warmup skipped: ${e.message}")
                isReady = false
            } finally {
                isInitializing = false
            }
        }.start()
    }
    
    private fun hasArCoreLibrary(): Boolean {
        return try {
            Class.forName("com.google.ar.core.Session")
            true
        } catch (e: ClassNotFoundException) {
            false
        }
    }
    
    private fun initializeArCoreIfAvailable(context: Context) {
        // This will only compile/run on withArcore flavor
        // On noArcore, hasArCoreLibrary() returns false above
        try {
            // Cast to the actual class (only available in withArcore)
            // Your ARCore init code here
            Log.d("HANS", "✓ ARCore ready")
        } catch (e: Exception) {
            throw e
        }
    }
    
    fun resume(): Any? = arSession
    fun pause() {}
    fun destroy() { arSession = null }
}