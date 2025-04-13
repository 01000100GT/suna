import {createClient} from "@/lib/supabase/server";
import AccountBillingStatus from "@/components/basejump/account-billing-status";

const returnUrl = process.env.NEXT_PUBLIC_URL as string;

export default async function PersonalAccountBillingPage() {
    const supabaseClient = createClient();
    const {data: personalAccount} = await supabaseClient.rpc('get_personal_account');

    return (
        <div className="space-y-6">
            <div>
                <h3 className="text-lg font-medium text-card-title">Billing</h3>
                <p className="text-sm text-foreground/70">
                    Manage your subscription and billing details.
                </p>
            </div>
            
            <AccountBillingStatus 
                accountId={personalAccount.account_id} 
                returnUrl={`${returnUrl}/dashboard/settings/billing`} 
            />
        </div>
    )
}