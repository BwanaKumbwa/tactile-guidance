package com.example.hans

import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import androidx.appcompat.app.AppCompatActivity

class SplashActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_splash)
        
        // Wait for ARCore warmup (or 2 seconds, whichever is shorter)
        Handler(Looper.getMainLooper()).postDelayed({
            startActivity(Intent(this, HomescreenActivity::class.java))
            finish()
        }, 2000)  // Adjust timing based on your preference
    }
}