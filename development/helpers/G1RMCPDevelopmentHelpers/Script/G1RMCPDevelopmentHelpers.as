// Помощники принадлежат инструменту G1R MCP. FHitResult обслуживается AngelScript.
UCLASS()
class UG1RMCPDevelopmentHelpers : UScriptWorldSubsystem
{
    UFUNCTION(BlueprintCallable)
    FString GetHelperVersion()
    {
        return "1.0.0-dev.29";
    }

    UFUNCTION(BlueprintCallable)
    bool HasWorld()
    {
        return GetWorld() != nullptr;
    }

    UFUNCTION(BlueprintCallable)
    ETraceTypeQuery GetVisibilityTraceChannel()
    {
        return UCollisionProfile::ConvertToTraceType(ECollisionChannel::ECC_Visibility);
    }

    UFUNCTION(BlueprintCallable)
    bool TraceImpactPoint(FVector Start, FVector End, ETraceTypeQuery Channel, bool TraceComplex,
                          const TArray<AActor>&in ActorsToIgnore, bool IgnoreSelf, FVector&out ImpactPoint)
    {
        if (GetWorld() == nullptr)
        {
            ImpactPoint = FVector::ZeroVector;
            return false;
        }

        FHitResult HitResult = FHitResult();
        bool Hit = System::LineTraceSingle(Start, End, Channel, TraceComplex, ActorsToIgnore,
                                          EDrawDebugTrace::None, HitResult, IgnoreSelf);
        ImpactPoint = Hit ? HitResult.ImpactPoint : FVector::ZeroVector;
        return Hit;
    }
}
