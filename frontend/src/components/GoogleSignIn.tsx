'use client';

import { useEffect, useCallback } from 'react';
import Script from 'next/script';
import { createClient } from '@/lib/supabase/client';

// Add type declarations for Google One Tap
declare global {
  interface Window {
    handleGoogleSignIn?: (response: GoogleSignInResponse) => void;
    google: {
      accounts: {
        id: {
          initialize: (config: GoogleInitializeConfig) => void;
          renderButton: (element: HTMLElement, options: GoogleButtonOptions) => void;
          prompt: (callback?: (notification: GoogleNotification) => void) => void;
          cancel: () => void;
        };
      };
    };
  }
}

// Define types for Google Sign-In
interface GoogleSignInResponse {
  credential: string;
  clientId?: string;
  select_by?: string;
}

interface GoogleInitializeConfig {
  client_id: string | undefined;
  callback: ((response: GoogleSignInResponse) => void) | undefined;
  nonce?: string;
  use_fedcm?: boolean;
  context?: string;
  itp_support?: boolean;
}

interface GoogleButtonOptions {
  type?: string;
  theme?: string;
  size?: string;
  text?: string;
  shape?: string;
}

interface GoogleNotification {
  isNotDisplayed: () => boolean;
  getNotDisplayedReason: () => string;
  isSkippedMoment: () => boolean;
  getSkippedReason: () => string;
  isDismissedMoment: () => boolean;
  getDismissedReason: () => string;
}

interface GoogleSignInProps {
  returnUrl?: string;
}

export default function GoogleSignIn({ returnUrl }: GoogleSignInProps) {
  const googleClientId = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID;

  const handleGoogleSignIn = useCallback(async (response: GoogleSignInResponse) => {
    try {
      const supabase = createClient();
      const { error } = await supabase.auth.signInWithIdToken({
        provider: 'google',
        token: response.credential,
      });

      if (error) throw error;
      window.location.href = returnUrl || "/dashboard";
    } catch (error) {
      console.error('Error signing in with Google:', error);
    }
  }, [returnUrl]);

  useEffect(() => {
    // Assign the callback to window object so it can be called from the Google button
    window.handleGoogleSignIn = handleGoogleSignIn;

    if (window.google && googleClientId) {
      window.google.accounts.id.initialize({
        client_id: googleClientId,
        callback: handleGoogleSignIn,
        use_fedcm: true,
        context: 'signin',
        itp_support: true
      });
    }

    return () => {
      // Cleanup
      delete window.handleGoogleSignIn;
      if (window.google) {
        window.google.accounts.id.cancel();
      }
    };
  }, [googleClientId, handleGoogleSignIn]);

  if (!googleClientId) {
    return (
      <button disabled className="w-full flex items-center justify-center bg-white dark:bg-background-secondary text-black dark:text-white border border-gray-300 dark:border-gray-700 rounded-lg py-3 px-4 opacity-60 cursor-not-allowed">
        <svg className="w-5 h-5 mr-2" viewBox="0 0 24 24">
          <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
          <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
          <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
          <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
        </svg>
        Google Sign-In Not Configured
      </button>
    );
  }

  return (
    <>
      {/* Hidden div for One Tap popup */}
      <div 
        id="g_id_onload"
        data-client_id={googleClientId}
        data-context="signin"
        data-ux_mode="popup"
        data-auto_prompt="false"
        data-itp_support="true"
        data-callback="handleGoogleSignIn"
      />
      {/* The actual sign-in button that will be shown */}
      <div
        className="g_id_signin flex justify-center"
        data-type="standard"
        data-size="large"
        data-theme="outline"
        data-text="sign_in_with"
        data-shape="rectangular"
        data-logo_alignment="left"
        data-width="100%"
      />
      <Script
        src="https://accounts.google.com/gsi/client"
        strategy="afterInteractive"
      />
    </>
  );
} 